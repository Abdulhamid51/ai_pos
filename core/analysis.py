"""Model yozgan pandas kodini tekshirib bajaradigan qatlam.

Bu faylning vazifasi — ishonchsiz kodni ishonchli chegarada ishlatish.
Uch qatlam himoya bor:

  1. AST tekshiruvi — kod bajarilishidan OLDIN daraxt bo'ylab o'qiladi:
     import, `while`, dunder (`__...__`) va fayl yozadigan metodlar taqiqlanadi.
  2. Cheklangan muhit — `builtins` o'rniga qisqa oq ro'yxat beriladi, ya'ni
     `open`, `eval`, `__import__` kabi nomlar umuman mavjud emas.
  3. Ma'lumot chegarasi — DataFrame allaqachon foydalanuvchi ruxsatiga qarab
     yig'ilgan (`datasets.py`), shuning uchun kod ko'rmasligi kerak bo'lgan
     qatorga yeta olmaydi.

To'liq izolyatsiya emas (buning uchun alohida jarayon yoki konteyner kerak),
lekin amaliy xavflarning asosiy qismini yopadi.
"""

import ast
import threading

import pandas as pd

# Kod bajarilishi uchun ajratilgan vaqt.
TIMEOUT = 10

# Foydalanuvchiga ko'rsatiladigan eng ko'p qator.
MAX_RESULT_ROWS = 200

# Modelga qaytariladigan "kichik natija" chegarasi. Bundan katta jadval
# modelga to'liq yuborilmaydi — foydalanuvchiga o'zi ko'rsatiladi.
SMALL_ROWS = 15

ENTRY_POINT = "javob"

# Kodda ishlatsa bo'ladigan nomlar.
SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "divmod": divmod, "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "len": len, "list": list, "map": map, "max": max, "min": min,
    "range": range, "reversed": reversed, "round": round, "set": set,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "True": True, "False": False, "None": None,
}

# Tashqi dunyoga chiqadigan yoki fayl bilan ishlaydigan metodlar.
FORBIDDEN_ATTRS = {
    "to_csv", "to_excel", "to_pickle", "to_parquet", "to_hdf", "to_sql",
    "to_clipboard", "to_feather", "to_stata", "to_gbq", "to_orc", "to_xml",
    "read_csv", "read_excel", "read_pickle", "read_sql", "read_json",
    "read_parquet", "read_html", "read_clipboard", "read_fwf", "read_table",
    "eval", "exec", "compile", "open", "system", "popen", "pipe",
}

# Umuman chaqirilmaydigan nomlar.
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "input", "exit", "quit", "breakpoint",
    "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr",
    "__import__", "memoryview", "object", "type", "super", "help",
}


class UnsafeCode(Exception):
    """Kod xavfsizlik tekshiruvidan o'tmadi."""


class CodeError(Exception):
    """Kod bajarilishida xato — sabab modelga qaytariladi, u tuzatib qayta uradi."""


# ---------------------------------------------------------------------------
# Tekshiruv
# ---------------------------------------------------------------------------

def validate(code):
    """Kodni bajarmasdan tekshiradi. Muammo bo'lsa `UnsafeCode` ko'taradi."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        raise CodeError(f"Sintaksis xatosi: {error}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise UnsafeCode("import ishlatib bo'lmaydi — faqat `df` va `pd` mavjud.")

        if isinstance(node, ast.While):
            raise UnsafeCode("`while` tsikli taqiqlangan — pandas amallaridan foydalaning.")

        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise UnsafeCode("global/nonlocal ishlatib bo'lmaydi.")

        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise UnsafeCode(f"Ichki atributga murojaat taqiqlangan: {node.attr}")
            if node.attr in FORBIDDEN_ATTRS:
                raise UnsafeCode(f"`{node.attr}` metodidan foydalanib bo'lmaydi.")

        if isinstance(node, ast.Name):
            if node.id.startswith("__") or node.id in FORBIDDEN_NAMES:
                raise UnsafeCode(f"`{node.id}` nomidan foydalanib bo'lmaydi.")

    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not any(f.name == ENTRY_POINT for f in functions):
        raise CodeError(f"Kodda `def {ENTRY_POINT}(df):` funksiyasi bo'lishi shart.")

    return tree


# ---------------------------------------------------------------------------
# Bajarish
# ---------------------------------------------------------------------------

def run(code, frame):
    """Kodni bajarib, `javob(df)` natijasini qaytaradi."""
    validate(code)

    namespace = {"__builtins__": SAFE_BUILTINS, "pd": pd}
    try:
        exec(compile(code, "<model_kodi>", "exec"), namespace)
    except Exception as error:
        raise CodeError(f"{type(error).__name__}: {error}")

    function = namespace.get(ENTRY_POINT)
    if not callable(function):
        raise CodeError(f"`{ENTRY_POINT}` funksiyasi topilmadi.")

    box = {}

    def target():
        try:
            box["result"] = function(frame.copy())
        except Exception as error:  # modelning o'z kodidagi xato
            box["error"] = f"{type(error).__name__}: {error}"

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(TIMEOUT)

    if worker.is_alive():
        raise CodeError(f"Kod {TIMEOUT} soniyada tugamadi — soddalashtiring.")
    if "error" in box:
        raise CodeError(box["error"])

    return box.get("result")


# ---------------------------------------------------------------------------
# Natijani ko'rinishga keltirish
# ---------------------------------------------------------------------------

def _clean(value):
    """NaN va sanalarni JSON'ga yaroqli holga keltiradi."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%d.%m.%Y %H:%M")
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def shape(result):
    """Natijani bir xil ko'rinishga soladi: jadval, qator yoki bitta qiymat.

    Qaytadi: (modelga_beriladigan_dict, foydalanuvchiga_ko'rsatiladigan_jadval_yoki_None)
    """
    if isinstance(result, pd.Series):
        result = result.reset_index()
        if result.shape[1] == 2:
            result.columns = [str(c) for c in result.columns]

    if isinstance(result, pd.DataFrame):
        # `groupby` natijasida guruh nomlari indeksga tushadi. Ularni ustunga
        # qaytarmasak, jadvalda "nima bo'yicha" degan ustun yo'qoladi.
        if not isinstance(result.index, pd.RangeIndex):
            result = result.reset_index()
        total = int(len(result))
        frame = result.head(MAX_RESULT_ROWS)
        columns = [str(c) for c in frame.columns]
        rows = [[_clean(v) for v in row] for row in frame.itertuples(index=False, name=None)]

        table = {"ustunlar": columns, "qatorlar": rows, "jami": total,
                 "qisqartirilgan": total > MAX_RESULT_ROWS}

        # Kichik natija — modelga to'liq beriladi, u gap qilib tushuntiradi.
        if total <= SMALL_ROWS and len(columns) <= 8:
            return {"turi": "jadval", "jami": total, "ustunlar": columns,
                    "qatorlar": rows}, None

        # Katta natija — modelga faqat namuna, to'lig'i foydalanuvchiga.
        return {
            "turi": "katta_jadval",
            "jami": total,
            "ustunlar": columns,
            "namuna": rows[:5],
            "izoh": "To'liq jadval foydalanuvchiga ko'rsatiladi. Uni takrorlama, "
                    "faqat qisqa xulosa yoz.",
        }, table

    if isinstance(result, (int, float, str, bool)) or result is None:
        return {"turi": "qiymat", "qiymat": _clean(result)}, None

    if isinstance(result, dict):
        return {"turi": "lugat", "qiymat": {str(k): _clean(v) for k, v in result.items()}}, None

    if isinstance(result, (list, tuple)):
        return {"turi": "royxat", "qiymat": [_clean(v) for v in list(result)[:MAX_RESULT_ROWS]]}, None

    return {"turi": "matn", "qiymat": str(result)[:2000]}, None
