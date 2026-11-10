# Limit Lamp

A small always-on-top Windows panel that shows your Claude subscription limits live: the **5-hour session**, the **weekly limit** and per-model weekly limits, with exact reset times, a pace indicator and a history graph. The background is translucent and the whole UI scales smoothly when you resize it.

![screenshot](docs/screenshot.png)

> Unofficial community project. Not affiliated with, endorsed by, or sponsored by Anthropic. "Claude" is a trademark of Anthropic.
> The interface is currently in Turkish (strings live in `app/limitlamp/model.py` and `render.py`).

## Two ways to get data

| | Needs sign-in | Network | Gives you |
|---|---|---|---|
| **Claude Code link** (`statusLine`) | no | none | 5-hour + weekly %, exact reset times. Updates whenever Claude Code gets a response. |
| **Account sign-in** (optional) | yes, once | only `claude.ai` / `api.anthropic.com` | Live values from your account (also other devices), per-model weekly limits, extra usage. Polled every ~3 minutes. |

You can use either or both. Without signing in, the program makes **no network connections at all**.

### About the account sign-in (read this before using it)

- You sign in **in your own browser**; your password is never typed into this program. You paste the code the page shows.
- Only the `user:profile` scope is requested. The token is encrypted with Windows DPAPI (tied to your Windows user) before it is written to disk; *Menu → sign out* deletes it.
- It uses Claude Code's **public OAuth client id** and the **undocumented** `api.anthropic.com/api/oauth/usage` endpoint (the same data Claude Code's `/usage` shows). This program identifies itself honestly as `LimitLamp/2.0`. The endpoint can change or be restricted at any time, and Anthropic's terms may limit third-party use of this login. **Use it at your own risk.** If you prefer to stay clear of that, use only the Claude Code link.

## Why Python?

On Windows 11 with *Smart App Control* enabled, any unsigned `.exe` you build yourself can be blocked. This project therefore ships as plain Python scripts that run on the already-signed `pythonw.exe` - nothing unsigned to block, no admin rights, no installer.

## Requirements

Windows 10/11, Python 3.9+ (python.org build with tkinter) and Pillow: `python -m pip install pillow`.

## Install

```powershell
.\install.ps1            # copies the app to %LOCALAPPDATA%\LimitLamp\app, Start Menu shortcut "Limit Lamp"
.\connect-claude.ps1     # optional: link Claude Code's statusLine (backs up settings.json first)
```

Then start **Limit Lamp** from the Start Menu and, if you want live account data, use *right-click -> Claude hesabına giriş yap*.
Undo: `.\connect-claude.ps1 -Remove`, `.\install.ps1 -Uninstall`.

`connect-claude.ps1` adds a single `statusLine` entry to `~/.claude/settings.json`. It refuses to overwrite an existing one, validates the JSON before and after, and keeps a timestamped backup. The feeder (`limitlamp_feed.py`) writes only the limits (never project paths, conversation content or tokens) to `%LOCALAPPDATA%\LimitLamp\`.

## Using it

- **Drag** to move; **drag any edge or corner** or **Ctrl + mouse wheel** to resize (everything scales together); **double-click the header** for compact mode.
- **Right-click** or the ⋯ button for the menu: background transparency, size presets, always-on-top, start with Windows, sign in/out, connection status.
- The header **lamp** turns green / amber / red with your highest usage; the tray icon shows the 5-hour percentage; a notification appears above 90 %.
- The white tick on a bar marks where you would be if you used the limit evenly across the window.

Try it with sample data: `pythonw app\limitlamp.pyw --demo`.

## Tests

```powershell
python tests\test_core.py      # feeder, parsing, DPAPI store, OAuth helpers (dummy values only)
python tests\selftest_gui.py   # drives a real window: zoom, resize, drag, compact, dialog, menu
```

## Limitations

- Account-wide values come from the server only when you sign in; the Claude Code link reflects the last Claude Code response.
- The unofficial usage endpoint's response shape may change (a key-names-only dump is written to `api-shape.txt` if parsing fails, so it can be fixed).

## License

MIT

---

# Limit Lamp (Türkçe)

Claude abonelik limitlerini (5 saatlik oturum, haftalık limit, modele özel limitler) kesin sıfırlanma zamanı, tempo göstergesi ve geçmiş grafiğiyle canlı gösteren, her zaman üstte duran küçük Windows paneli. Arka plan yarı saydam; pencereyi büyütüp küçültünce tüm arayüz orantılı ölçeklenir.

> Resmî olmayan topluluk projesidir; Anthropic ile bağlantılı değildir.

**İki veri yolu:** (1) *Claude Code bağlantısı* - giriş yok, ağ yok; (2) *Hesap girişi* (isteğe bağlı) - tarayıcıda giriş yaparsın, şifren programa girilmez; token Windows DPAPI ile şifrelenir. Giriş modu Claude Code'un herkese açık OAuth kimliğini ve belgelenmemiş bir kullanım uç noktasını kullanır; **kendi sorumluluğunda kullan**. Giriş yapmazsan program hiçbir ağ bağlantısı kurmaz.

**Neden Python?** Windows 11'de Akıllı Uygulama Denetimi açıkken kendi derlediğin imzasız `.exe` engellenebilir; imzalı `pythonw.exe` üzerinde çalışan betikler engellenmez.

**Kurulum:** `python -m pip install pillow` · `.\install.ps1` · (isteğe bağlı) `.\connect-claude.ps1` · Başlat menüsünden **Limit Lamp**. Geri alma: `.\connect-claude.ps1 -Remove`, `.\install.ps1 -Uninstall`.
