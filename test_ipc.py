import MetaTrader5 as mt5

path = r"C:\Program Files\XM Global SC MT5 Terminal\terminal64.exe"

print("Conectando con:", path)

ok = mt5.initialize(path=path, timeout=60000)

if not ok: print("ERROR:", mt5.last_error()) 
else: print("CONEXIÓN IPC CORRECTA")
print("Terminal:", mt5.terminal_info()) 
print("Cuenta:", mt5.account_info())

mt5.shutdown()