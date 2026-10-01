"""
Password protection for the Tag Follow-Up board.

  python encrypt_board.py page   board.html  site/index.html   # password-locked web page
  python encrypt_board.py lock   tag_cache.json  cache.enc     # encrypt the cache
  python encrypt_board.py unlock cache.enc  tag_cache.json     # decrypt the cache

The password comes from the BOARD_PASSWORD environment variable.
AES-256-GCM with a PBKDF2-SHA256 key, so the page decrypts in the browser
with the same password and nothing readable is ever published.
"""
import base64, json, os, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

ITER = 310000


def key_for(password, salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(password.encode())


def seal(data, password):
    salt, iv = os.urandom(16), os.urandom(12)
    return salt, iv, AESGCM(key_for(password, salt)).encrypt(iv, data, None)


def b64(b):
    return base64.b64encode(b).decode()


LOADER = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>504 Tag Follow-Up</title>
<style>
:root{--bg:#f3f4f7;--surface:#fff;--line:#d9dce5;--fg:#181b24;--fg2:#4a5063;--accent:#3346d3;--on:#fff;--crit:#c43434}
@media (prefers-color-scheme:dark){:root{--bg:#12141a;--surface:#1b1e26;--line:#2f3442;--fg:#eceef4;--fg2:#b2b7c6;--accent:#8e9bff;--on:#12141a;--crit:#ff7f7f;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh;display:grid;place-items:center;padding:16px}
form{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:24px;width:100%;max-width:360px;display:flex;flex-direction:column;gap:12px}
h1{margin:0;font-size:22px}p{margin:0;color:var(--fg2);font-size:14px}
input[type=password]{border:1px solid var(--line);background:var(--surface);color:var(--fg);border-radius:8px;padding:10px 12px;font:inherit}
button{border:0;background:var(--accent);color:var(--on);border-radius:8px;padding:10px;font:600 15px inherit;cursor:pointer}
label{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--fg2)}
.err{color:var(--crit);font-size:13px;min-height:1.2em}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style></head><body>
<form id="f"><h1>504 Tag Follow-Up</h1><p>Enter the team password to open the board.</p>
<input id="pw" type="password" autocomplete="current-password" placeholder="Password" aria-label="Password" required autofocus>
<label><input id="rem" type="checkbox"> Remember on this device</label>
<button type="submit" id="go">Open board</button><div class="err" id="err" role="alert"></div></form>
<script>
const P=__PAYLOAD__;
const ub=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function decrypt(pw){
  const base=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:ub(P.salt),iterations:P.iter,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['decrypt']);
  return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:ub(P.iv)},key,ub(P.ct)));
}
const show=html=>{document.open();document.write(html);document.close()};
const LK='tagfu-board-pw';
document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();
  const pw=document.getElementById('pw').value,rem=document.getElementById('rem').checked,go=document.getElementById('go'),err=document.getElementById('err');
  go.textContent='Opening…';err.textContent='';
  let html;try{html=await decrypt(pw)}catch(x){go.textContent='Open board';err.textContent="That password didn't work. Check it and try again.";return}
  try{rem?localStorage.setItem(LK,pw):localStorage.removeItem(LK)}catch(x){}
  show(html)});
(async()=>{let s=null;try{s=localStorage.getItem(LK)}catch(x){}if(!s)return;try{show(await decrypt(s))}catch(x){try{localStorage.removeItem(LK)}catch(y){}}})();
</script></body></html>
"""


def main():
    mode, src, dst = sys.argv[1:4]
    pw = os.environ.get("BOARD_PASSWORD", "")
    if len(pw) < 8:
        sys.exit("Set BOARD_PASSWORD (8+ characters).")
    raw = open(src, "rb").read()
    if mode == "page":
        salt, iv, ct = seal(raw, pw)
        payload = json.dumps({"salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "iter": ITER})
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        open(dst, "w", encoding="utf-8").write(LOADER.replace("__PAYLOAD__", payload))
    elif mode == "lock":
        salt, iv, ct = seal(raw, pw)
        open(dst, "wb").write(salt + iv + ct)
    elif mode == "unlock":
        salt, iv, ct = raw[:16], raw[16:28], raw[28:]
        open(dst, "wb").write(AESGCM(key_for(pw, salt)).decrypt(iv, ct, None))
    else:
        sys.exit("mode must be page, lock or unlock")


if __name__ == "__main__":
    main()
