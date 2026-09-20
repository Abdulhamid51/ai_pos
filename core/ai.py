"""AI qatlami: modelga asboblar berish va suhbatni olib borish (RAG'ning "G" qismi).

Ishlash tartibi:

  1. foydalanuvchi savoli modelga yuboriladi, u bilan birga asboblar ro'yxati;
  2. model kerak bo'lsa asbob chaqiradi — masalan "savdo_hisoboti(davr='kecha')";
  3. biz o'sha funksiyani `retrieval.py` orqali bajaramiz va natijani qaytaramiz;
  4. model natija asosida javob yozadi.

Ya'ni raqamni har doim Django hisoblaydi, model faqat o'qiydi va tushuntiradi.
Model hech qachon ma'lumotni o'zgartira olmaydi: asboblar ro'yxatida faqat
o'qish amallari bor.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone

from . import analysis, datasets, retrieval

logger = logging.getLogger(__name__)

# Bitta savolga nechta asbob chaqirish sikli ruxsat etiladi.
MAX_STEPS = 6

SYSTEM_INSTRUCTION = """Sen "AI POS" savdo tizimining ichki yordamchisisan.
Suhbatdoshing — do'kon xodimi (direktor, kassir yoki sotuvchi).

Qoidalar:
- Doim o'zbek tilida, qisqa va aniq javob ber. Ortiqcha muqaddima yozma.
- Raqam kerak bo'lsa — albatta asbob chaqir. Summani, foydani yoki qoldiqni
  o'zing hisoblama va xotirangdan yozma.
- Asbob qaytargan ma'lumotdan tashqari hech narsa qo'shma. Ma'lumot topilmasa,
  buni ochiq ayt.
- Javobda raqamlarni sodda ko'rsat, valyuta nomini yozib qo'y.
- Sen ma'lumotni o'zgartira olmaysan: narx, qoldiq yoki chekka tegmaysan.
  Foydalanuvchi biror narsani o'zgartirishni so'rasa, qaysi sahifada qilishini
  ayt (Kassa, Mahsulotlar, Mijozlar, Hisobotlar).
- Javobni oddiy matn bilan yoz, kerak bo'lsa qisqa ro'yxat ishlat.
- Javobda markdown jadval (| ... |) yozma — u xunuk chiqadi. Jadval kerak
  bo'lsa `pandas_hisobla` dan foydalan: natija foydalanuvchiga chiroyli
  jadval bo'lib ko'rsatiladi.
- Oldingi javobingdagi raqamni takrorlashdan ko'ra, qayta hisoblagan ma'qul:
  ma'lumot o'zgargan bo'lishi mumkin.
"""


# ---------------------------------------------------------------------------
# Asboblar — model faqat shu funksiyalarni chaqira oladi
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "name": "mahsulot_qidir",
        "description": (
            "Mahsulotlarni ma'no bo'yicha qidiradi. Nomi aniq bo'lmagan savollarda "
            "ham ishlaydi: 'sovuq ichimlik', 'bolalar uchun kiyim', 'bosh og'rig'iga dori'. "
            "Narxi, qoldig'i va filialini qaytaradi."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "so_rov": {
                    "type": "string",
                    "description": "Qidiruv matni — foydalanuvchi so'raganidek.",
                }
            },
            "required": ["so_rov"],
        },
    },
    {
        "type": "function",
        "name": "savdo_hisoboti",
        "description": (
            "Tanlangan davr uchun savdo ko'rsatkichlari: tushum, foyda, cheklar soni, "
            "o'rtacha chek, eng ko'p sotilgan mahsulotlar va to'lov turlari."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "davr": {
                    "type": "string",
                    "enum": ["bugun", "kecha", "7kun", "30kun", "shu_oy"],
                    "description": "Hisobot davri.",
                }
            },
            "required": ["davr"],
        },
    },
    {
        "type": "function",
        "name": "ombor_ogohlantirishlari",
        "description": (
            "Diqqat talab qiladigan tovarlar: qoldig'i minimal darajadan pastga tushganlar "
            "va 30 kun ichida yaroqlilik muddati tugaydiganlar."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "mijoz_qidir",
        "description": (
            "Mijozlarni ism yoki telefon bo'yicha qidiradi va qarzini ko'rsatadi. "
            "So'rov bo'sh qoldirilsa — eng katta qarzdorlar ro'yxatini qaytaradi."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "so_rov": {
                    "type": "string",
                    "description": "Mijoz ismi yoki telefon raqami. Qarzdorlar uchun bo'sh qoldiring.",
                }
            },
        },
    },
    {
        "type": "function",
        "name": "toplamni_korish",
        "description": (
            "Ma'lumot to'plamining ustunlari, turlari va 5 ta namuna qatorini qaytaradi. "
            "pandas kodi yozishdan oldin shuni chaqir."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "toplam": {
                    "type": "string",
                    "enum": ["cheklar", "chek_qatorlari", "mahsulotlar", "mijozlar", "ombor_harakatlari"],
                    "description": "Ko'riladigan to'plam nomi.",
                }
            },
            "required": ["toplam"],
        },
    },
    {
        "type": "function",
        "name": "pandas_hisobla",
        "description": (
            "Tanlangan to'plam ustida pandas kodini bajaradi va natijani qaytaradi. "
            "Kodda `def javob(df):` funksiyasi bo'lishi shart."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "toplam": {
                    "type": "string",
                    "enum": ["cheklar", "chek_qatorlari", "mahsulotlar", "mijozlar", "ombor_harakatlari"],
                    "description": "Kod ishlaydigan to'plam.",
                },
                "kod": {
                    "type": "string",
                    "description": "Python kodi: `def javob(df):` va `return` bilan natija.",
                },
            },
            "required": ["toplam", "kod"],
        },
    },
]


ANALYSIS_GUIDE = """
## Erkin tahlil (pandas)

Yuqoridagi tayyor asboblar yetmasa — masalan "qaysi kassir ko'proq chegirma berdi",
"haftaning qaysi kunida savdo yaxshi", "qaysi mahsulot foyda bermayapti" kabi
savollarda — pandas tahlilidan foydalan:

1. avval `toplamni_korish` bilan ustunlar va namuna qatorlarni ko'r;
2. so'ng `pandas_hisobla` ga kod yoz.

Kod qoidalari:
- `def javob(df):` funksiyasi bo'lishi shart, natijani `return` qil;
- faqat `df` va `pd` mavjud. import yo'q, fayl bilan ishlash yo'q, `while` yo'q;
- natija DataFrame, Series yoki bitta qiymat bo'lsin;
- ustun nomlarini o'zing o'ylab topma — faqat `toplamni_korish` ko'rsatganini ishlat.

Natija katta jadval bo'lsa, u foydalanuvchiga o'zi ko'rsatiladi: sen uni
takrorlama, faqat bir-ikki gapda xulosa yoz.

## Mavjud to'plamlar

{catalog}
"""


class AIError(Exception):
    """AI javob bera olmadi — sabab foydalanuvchiga ko'rsatiladi."""


def is_configured():
    """Kalit sozlanganmi?"""
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


def client_or_error():
    """Gemini klienti. Kutubxona yoki kalit bo'lmasa — tushunarli xato."""
    try:
        from google import genai
    except ImportError:
        raise AIError("google-genai kutubxonasi o'rnatilmagan.")

    if not is_configured():
        raise AIError("GEMINI_API_KEY sozlanmagan — .env faylini tekshiring.")

    return genai.Client(api_key=settings.GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# Kontekst: o'zgarmaydigan, arzon ma'lumot
# ---------------------------------------------------------------------------

def pos_context(user):
    """Har bir savolga qo'shiladigan qisqa ma'lumot: kim, qayerda, qachon.

    Raqamlar bu yerda yo'q — ular asboblar orqali, faqat kerak bo'lganda olinadi.
    Shunda har bir savol uchun ortiqcha so'rov ham, ortiqcha token ham sarflanmaydi.
    """
    branch = user.active_branch
    lines = [
        f"Sana: {timezone.localdate():%d.%m.%Y}",
        f"Xodim: {user} ({user.get_role_display()})",
        f"Filial: {branch.name if branch else 'tanlanmagan'}",
        f"Valyuta: {settings.POS_CURRENCY}",
    ]
    if branch:
        lines.append(f"Savdo turi: {branch.get_trade_type_display()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Asboblarni bajarish
# ---------------------------------------------------------------------------

def _run_tool(name, arguments, user, tables):
    """Model so'ragan asbobni bajaradi.

    Muhim: filiallar va kompaniya har doim `user` dan olinadi — model ularni
    o'zi tanlay olmaydi. Shuning uchun kassir boshqa filialning ma'lumotini
    so'rab ololmaydi, qanday yozmasin.
    """
    branches = user.visible_branches()

    if name == "mahsulot_qidir":
        return retrieval.search_products(arguments.get("so_rov", ""), branches)

    if name == "savdo_hisoboti":
        return retrieval.sales_report(branches, arguments.get("davr", "bugun"))

    if name == "ombor_ogohlantirishlari":
        return retrieval.stock_watch(branches)

    if name == "mijoz_qidir":
        company = user.company or (user.active_branch.company if user.active_branch else None)
        if not company:
            return {"xato": "Kompaniya aniqlanmadi."}
        return retrieval.find_customers(company, arguments.get("so_rov", ""))

    if name == "toplamni_korish":
        try:
            return datasets.describe(arguments.get("toplam", ""), user)
        except KeyError:
            return {"xato": "Bunday to'plam yo'q."}

    if name == "pandas_hisobla":
        toplam = arguments.get("toplam", "")
        try:
            frame = datasets.load(toplam, user)
        except KeyError:
            return {"xato": "Bunday to'plam yo'q."}
        code = arguments.get("kod", "")
        # Model yozgan kod jurnalga tushadi — nosozlikni tekshirish uchun.
        logger.info("pandas kodi (%s):\n%s", toplam, code)
        try:
            result = analysis.run(code, frame)
        except (analysis.UnsafeCode, analysis.CodeError) as error:
            # Xato modelga qaytadi — u kodini tuzatib qayta urinishi mumkin.
            return {"xato": str(error)}
        shaped, table = analysis.shape(result)
        if table:
            table["sarlavha"] = f"{toplam} · {table['jami']} qator"
            tables.append(table)
        return shaped

    return {"xato": f"Noma'lum asbob: {name}"}


def _label_of(name, arguments):
    """Foydalanuvchiga ko'rsatiladigan manba nomi."""
    if name == "mahsulot_qidir":
        return f"Mahsulot qidiruvi: “{arguments.get('so_rov', '')}”"
    if name == "savdo_hisoboti":
        return f"Savdo hisoboti: {arguments.get('davr', 'bugun')}"
    if name == "ombor_ogohlantirishlari":
        return "Ombor ogohlantirishlari"
    if name == "mijoz_qidir":
        query = arguments.get("so_rov", "")
        return f"Mijoz qidiruvi: “{query}”" if query else "Qarzdorlar ro'yxati"
    if name == "toplamni_korish":
        return f"To'plam ko'rildi: {arguments.get('toplam', '')}"
    if name == "pandas_hisobla":
        return f"Pandas tahlili: {arguments.get('toplam', '')}"
    return name


def _arguments_of(step):
    """Asbob argumentlari dict yoki JSON matn bo'lib kelishi mumkin."""
    raw = getattr(step, "arguments", None) or {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return dict(raw)


# ---------------------------------------------------------------------------
# Suhbat
# ---------------------------------------------------------------------------

def chat_reply(message, user, previous_id=None):
    """Savolga javob qaytaradi: (javob, suhbat_id, manbalar, jadvallar)."""
    client = client_or_error()

    guide = ANALYSIS_GUIDE.format(catalog=datasets.catalog())
    instruction = f"{SYSTEM_INSTRUCTION}\n{guide}\n\n## Hozirgi holat\n{pos_context(user)}"
    params = {
        "model": settings.GEMINI_MODEL,
        "system_instruction": instruction,
        "input": message,
        "tools": TOOLS,
    }
    if previous_id:
        params["previous_interaction_id"] = previous_id

    sources = []
    tables = []   # foydalanuvchiga to'g'ridan-to'g'ri ko'rsatiladigan katta natijalar

    for _ in range(MAX_STEPS):
        interaction = _create(client, params, allow_retry=bool(params.get("previous_interaction_id")))

        calls = [
            step for step in (interaction.steps or [])
            if getattr(step, "type", "") == "function_call"
        ]

        if not calls:
            text = (interaction.output_text or "").strip()
            if not text:
                raise AIError("Model bo'sh javob qaytardi.")
            return text, interaction.id, sources, tables

        # Model so'ragan barcha asboblarni bajaramiz va natijalarni qaytaramiz.
        results = []
        for call in calls:
            arguments = _arguments_of(call)
            try:
                data = _run_tool(call.name, arguments, user, tables)
            except Exception as error:
                data = {"xato": f"{type(error).__name__}: {error}"}
            sources.append(_label_of(call.name, arguments))
            results.append({
                "type": "function_result",
                "name": call.name,
                "call_id": call.id,
                "result": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
            })

        params = {
            "model": settings.GEMINI_MODEL,
            "system_instruction": instruction,
            "input": results,
            "tools": TOOLS,
            "previous_interaction_id": interaction.id,
        }

    raise AIError("Javob juda ko'p qadam talab qildi — savolni soddalashtirib ko'ring.")


def _create(client, params, allow_retry=False):
    """Modelga murojaat. Eski suhbat id'si yaroqsiz bo'lsa — tarixsiz qayta urinadi."""
    try:
        return client.interactions.create(**params)
    except Exception as error:
        if allow_retry and "previous_interaction_id" in params:
            retry = dict(params)
            retry.pop("previous_interaction_id")
            try:
                return client.interactions.create(**retry)
            except Exception as retry_error:
                raise AIError(_message_of(retry_error))
        raise AIError(_message_of(error))


def _message_of(error):
    """Xato matnini foydalanuvchiga ko'rsatsa bo'ladigan holga keltiradi."""
    text = str(error)
    if len(text) > 300:
        text = text[:300] + "…"
    return f"{type(error).__name__}: {text}"
