"""Groq: превращает свободный текст в список действий (JSON) и распознаёт голос (Whisper).

Бесплатный лимит маленький, поэтому: промпт короткий, при 429 берём следующую модель,
а простые команды и записи вообще не доходят до ИИ (см. quick.py).
"""
from __future__ import annotations

import json
import re
import time
from datetime import timedelta

import config
import net
import util


class LLMUnavailable(Exception):
    pass


def build_system(ref=None) -> str:
    ref = ref or util.today()
    tom = (ref + timedelta(days=1)).isoformat()
    y = ref.year if ref.month < 3 else ref.year + 1
    mar1 = f"{y}-03-01"
    nov3 = f"{ref.year if ref.month <= 11 else ref.year + 1}-11-03"
    return f"""You are the parser of a personal assistant bot (money, tasks, debts, savings, subscriptions, diary). The user writes Russian, casual, with typos, often voice transcripts.
Reply with ONLY valid JSON: {{"actions":[...]}}. One message may hold several actions. Never invent facts or numbers. If you cannot tell what the user wants, return a "say" action with a short clarifying question in Russian.

ACTION TYPES (omit unknown fields):
expense|income {{amount, currency, category, description, date?, emoji?}}
edit_tx {{id:"last"|N, amount?, currency?, category?, description?, date?, kind?}}  (fix a transaction)
task {{text, due?, remind?}}  (due/remind = "YYYY-MM-DD" or "YYYY-MM-DD HH:MM")
edit_task {{id|query, new_text?, due?, remind?}}   done {{id|query}}
debt {{person, direction:"owed_to_me"|"i_owe", amount, currency?, note?}}
debt_pay {{person, amount?, direction?}} (no amount = whole debt)   debt_forgive {{person}}
stash {{name, target?, deadline?, currency?}} (create goal/savings)   stash_move {{name?, amount, currency?, direction:"in"|"out"}}
sub {{name, kind?:"expense"|"income", amount (0 if only a deadline), currency?, period?:"month"|"year"|"once", day? (1-31), next_due?, category?}}
sub_edit {{id|query, amount?, currency?, next_due?, day?, period?, new_name?, active?}}   sub_paid {{id|query, amount?}}
journal {{kind:"workout|food|sleep|weight|mood|note", text, value?, date?}}
set_balance {{amount, currency?}} ("now I have X")   set_budget {{category?, amount}} (monthly limit; no category = whole month; 0 removes)
category {{op:"rename"|"merge", from, to}}   delete {{entity:"tx|task|debt|stash|sub|journal|category", id|name}}
restore {{entity?, id?}}   undo {{}}   rate {{currency, value|"auto"}}   settings {{key:"morning"|"weekly", value:"on"|"off"}}
query {{what:"balance|stats|tasks|debts|stashes|subs|recent|journal|search|history|categories|rates|settings|brief|weekly|help", period?:"today|yesterday|week|last_week|last7|last30|month|last_month|year|all", category?, text?, kind?}}
say {{text}}

RULES
- Amounts are numbers: "50к"=50000, "пятихатка"=500, "косарь"=1000, "полторы тысячи"=1500. Default currency = base_currency; $ / доллар / бакс = USD; € / евро = EUR. Set currency only if the user named one.
- Prefer an existing category from the list; invent a new one only if none fits (short Russian noun in nominative, e.g. "Питомцы") and add "emoji".
- Dates: use the calendar from the context. "завтра в 10" -> "{tom} 10:00". A weekday name = the nearest FUTURE date. No time said -> date only. Never put a reminder in the past.
- Corrections like "это не еда, а транспорт" / "было 400" -> edit_tx with id "last" (or the id from "recent").
- "отмени" -> undo. "верни #12" -> restore. Regular payments ("каждое 14-е", "до 3 ноября", "раз в год") -> sub. A bare deadline without a price -> sub with amount 0.
- Questions about the user's own data -> query ("сколько потратил на еду" -> stats with category; "когда последний раз ..." -> search). Small talk/advice -> say (max 2 short sentences, Russian).
- Output JSON only, no markdown.

EXAMPLES
"шаурма 300 и метро 150" -> {{"actions":[{{"type":"expense","amount":300,"category":"Еда","description":"шаурма"}},{{"type":"expense","amount":150,"category":"Транспорт","description":"метро"}}]}}
"пришла зарплата 50к" -> {{"actions":[{{"type":"income","amount":50000,"category":"Зарплата","description":"зарплата"}}]}}
"купил игру за 15 евро" -> {{"actions":[{{"type":"expense","amount":15,"currency":"EUR","category":"Развлечения","description":"игра"}}]}}
"это не еда а транспорт" -> {{"actions":[{{"type":"edit_tx","id":"last","category":"Транспорт"}}]}}
"напомни завтра в 10 сдать отчёт" -> {{"actions":[{{"type":"task","text":"сдать отчёт","due":"{tom} 10:00"}}]}}
"одолжил Васе пятихатку" -> {{"actions":[{{"type":"debt","person":"Вася","direction":"owed_to_me","amount":500}}]}}
"Вася вернул 300" -> {{"actions":[{{"type":"debt_pay","person":"Вася","amount":300}}]}}
"отложил 2000 в заначку" -> {{"actions":[{{"type":"stash_move","name":"Заначка","amount":2000,"direction":"in"}}]}}
"хочу накопить 60к на ноутбук до марта" -> {{"actions":[{{"type":"stash","name":"Ноутбук","target":60000,"deadline":"{mar1}"}}]}}
"Claude Pro 20$ каждое 14-е" -> {{"actions":[{{"type":"sub","name":"Claude Pro","amount":20,"currency":"USD","period":"month","day":14,"category":"Подписки"}}]}}
"сервер 300 в месяц, оплачен до 3 ноября" -> {{"actions":[{{"type":"sub","name":"Сервер","amount":300,"period":"month","next_due":"{nov3}","category":"Сервер"}}]}}
"жим 60 на 3 по 8" -> {{"actions":[{{"type":"journal","kind":"workout","text":"жим 60 кг, 3x8"}}]}}
"сколько потратил на еду за неделю" -> {{"actions":[{{"type":"query","what":"stats","category":"Еда","period":"week"}}]}}
"когда я последний раз был у стоматолога" -> {{"actions":[{{"type":"query","what":"search","text":"стоматолог"}}]}}"""


def parse_json_text(content: str):
    if not content:
        return None
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except ValueError:
            return None
    return None


def to_actions(obj) -> list[dict]:
    if isinstance(obj, list):
        return [a for a in obj if isinstance(a, dict)]
    if isinstance(obj, dict):
        if isinstance(obj.get("actions"), list):
            return [a for a in obj["actions"] if isinstance(a, dict)]
        if "type" in obj:
            return [obj]
    return []


class LLM:
    def __init__(self, key: str | None = None, models: list[str] | None = None, base: str | None = None,
                 on_usage=None):
        self.key = config.GROQ_API_KEY if key is None else key
        self.models = models or config.LLM_MODELS
        self.base = (base or config.GROQ_BASE).rstrip("/")
        self.on_usage = on_usage
        self.cooldown: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    def _cooldown(self, model: str, e: net.HTTPError) -> None:
        body = e.text().casefold()
        wait = 30.0
        ra = e.headers.get("retry-after") or e.headers.get("Retry-After")
        try:
            wait = max(float(ra), 5.0)
        except (TypeError, ValueError):
            if "per day" in body or "tpd" in body or "rpd" in body:
                wait = 3600.0
        self.cooldown[model] = time.time() + min(wait, 6 * 3600)

    def chat_json(self, messages: list[dict], max_tokens: int = 700):
        if not self.enabled:
            raise LLMUnavailable("нет ключа GROQ_API_KEY")
        last = "все модели заняты"
        for model in self.models:
            if self.cooldown.get(model, 0) > time.time():
                continue
            payload = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens,
                       "response_format": {"type": "json_object"}}
            try:
                data = net.post_json(self.base + "/chat/completions", payload, self._headers(), timeout=40)
            except net.HTTPError as e:
                if e.status in (401, 403):
                    raise LLMUnavailable("Groq не принял ключ (проверь GROQ_API_KEY)")
                if e.status == 429:
                    self._cooldown(model, e)
                    last = f"лимит {model}"
                    continue
                if e.status == 400 and "failed_generation" in e.text():
                    try:
                        fg = json.loads(e.text())["error"]["failed_generation"]
                        obj = parse_json_text(fg)
                        if obj is not None:
                            return obj
                    except (ValueError, KeyError, TypeError):
                        pass
                last = f"{model}: HTTP {e.status}"
                continue
            except (OSError, ValueError) as e:
                last = f"{model}: {e}"
                continue
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                last = f"{model}: странный ответ"
                continue
            if self.on_usage:
                try:
                    self.on_usage(model, int((data.get("usage") or {}).get("total_tokens") or 0))
                except Exception:  # noqa: BLE001
                    pass
            obj = parse_json_text(content)
            if obj is not None:
                return obj
            last = f"{model}: не JSON"
        raise LLMUnavailable(last)

    def parse(self, text: str, context: str, history: list[str] | None = None) -> list[dict]:
        user = context + "\n"
        if history:
            user += "previous user messages: " + " | ".join(h[:120] for h in history[-2:]) + "\n"
        user += "MESSAGE: " + text
        obj = self.chat_json([{"role": "system", "content": build_system()},
                              {"role": "user", "content": user}])
        return to_actions(obj)

    def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str:
        if not self.enabled:
            raise LLMUnavailable("нет ключа GROQ_API_KEY")
        body, ctype = net.multipart(
            {"model": config.STT_MODEL, "language": "ru", "response_format": "json", "temperature": "0"},
            {"file": (filename, audio)})
        try:
            _, raw, _ = net.request("POST", self.base + "/audio/transcriptions",
                                    {**self._headers(), "Content-Type": ctype}, body, timeout=90)
        except net.HTTPError as e:
            if e.status == 429:
                raise LLMUnavailable("лимит распознавания голоса, попробуй позже")
            if e.status in (401, 403):
                raise LLMUnavailable("Groq не принял ключ")
            raise LLMUnavailable(f"ошибка распознавания (HTTP {e.status})")
        except OSError as e:
            raise LLMUnavailable(f"нет связи с Groq: {e}")
        try:
            return str(json.loads(raw.decode("utf-8")).get("text", "")).strip()
        except ValueError:
            raise LLMUnavailable("странный ответ распознавания")
