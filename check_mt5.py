"""
Diagnóstico de conexión con MetaTrader 5.  Uso:  python check_mt5.py
No opera ni envía órdenes. No muestra la contraseña.
"""
from __future__ import annotations

import struct
import sys

from config import get_settings
from src.market_data import _import_mt5, connect_mt5


def main() -> int:
    s = get_settings()
    print(f"Python: {struct.calcsize('P') * 8} bits (debe ser 64)")
    print(f"Cuenta en .env: {s.mt5.login or '(vacío)'} | Servidor: {s.mt5.server or '(vacío)'} | "
          f"Ruta: {s.mt5.path or '(automática)'}")

    mt5 = _import_mt5()
    if mt5 is None:
        print("\nFALLO: la librería MetaTrader5 no está instalada.  Ejecuta: pip install MetaTrader5")
        return 1

    try:
        connect_mt5(mt5, s.mt5)
    except RuntimeError as exc:
        print(f"\nFALLO: {exc}")
        print("\nQué hacer:")
        print("  1. Cierra MT5 (Administrador de tareas -> terminal64.exe) y ábrelo tú, ya con sesión iniciada.")
        print("  2. Abre PowerShell y MT5 con los mismos permisos (ambos normales, sin administrador).")
        print("  3. Si 'login' falla: revisa MT5_LOGIN, MT5_PASSWORD y MT5_SERVER (el servidor exacto aparece")
        print("     en MT5 > Archivo > Abrir una cuenta / o en la barra de título del terminal).")
        print("  4. Si hay varios MT5 instalados, pon MT5_PATH=ruta\\terminal64.exe en .env")
        return 1

    try:
        info, term = mt5.account_info(), mt5.terminal_info()
        modes = {mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO", mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONCURSO",
                 mt5.ACCOUNT_TRADE_MODE_REAL: "REAL"}
        print("\nCONEXIÓN OK")
        print(f"  Tipo de cuenta : {modes.get(info.trade_mode, info.trade_mode)}")
        print(f"  Cuenta/servidor: {info.login} / {info.server}")
        print(f"  Moneda         : {info.currency}  {'(OK)' if info.currency == 'USD' else '(el bot real exige USD)'}")
        print(f"  Balance/equity : {info.balance:.2f} / {info.equity:.2f}")
        print(f"  Cuenta puede operar: {info.trade_allowed}")
        print(f"  Algo Trading activo: {term.trade_allowed}  "
              f"{'' if term.trade_allowed else '<- actívalo en el botón verde de MT5'}")
        sym = s.market.symbol
        found = mt5.symbol_select(sym, True)
        print(f"  Símbolo {sym}: {'disponible' if found else 'NO disponible con ese nombre exacto'}")
        if not found:
            similar = mt5.symbols_get(f"*{sym[:6]}*") or []
            names = [x.name for x in similar][:10]
            print(f"  Nombres parecidos en tu broker: {', '.join(names) if names else '(ninguno)'}")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
