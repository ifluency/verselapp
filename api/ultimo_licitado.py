import os
import json
import traceback
from datetime import datetime, date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import psycopg2
from psycopg2.extras import RealDictCursor


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.fromisoformat(str(v)).date()
    except Exception:
        return None


def _fmt_date_br(d: date | None) -> str:
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def _pregao_from_id_compra(id_compra: str | None) -> str:
    if not id_compra:
        return ""
    s = str(id_compra).strip()
    if len(s) < 9:
        return ""
    year = s[-4:]
    num5 = s[-9:-4]
    if not (year.isdigit() and num5.isdigit()):
        return ""
    if num5.startswith("9"):
        num = num5[1:]
    else:
        num = num5
    try:
        return f"{int(num):03d}/{int(year):04d}"
    except Exception:
        return ""


def _compra_link(id_compra: str | None) -> str:
    if not id_compra:
        return ""
    s = str(id_compra).strip()
    if not s:
        return ""
    return (
        "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/"
        f"acompanhamento-compra?compra={s}"
    )


def _to_float(x):
    """Converte números do banco (Decimal/float/int) e também strings PT-BR (ex: 'R$ 1.234,5600')."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        s = s.replace("R$", "").replace(" ", "")
        # remove separador de milhar e troca vírgula por ponto
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except Exception:
            return None
    try:
        return float(x)
    except Exception:
        return None


def _pick(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] is not None and str(d[k]).strip() != "":
            return d[k]
    return default


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes
