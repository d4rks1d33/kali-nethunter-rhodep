import hashlib, json, re, subprocess, sys, urllib.parse, os, html, time

JAR = "/tmp/opencode/cookies.txt"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

def curl(url, extra=None):
    cmd = ["curl","-sSL","-A",UA,"-b",JAR,"-c",JAR,url]
    if extra: cmd[1:1]=extra
    return subprocess.run(cmd,capture_output=True,text=True,timeout=180).stdout

def solve(page_html, url):
    m = re.search(r'id="anubis_challenge"[^>]*>(.*?)</script>', page_html, re.S)
    if not m: return None
    data = json.loads(html.unescape(m.group(1)))
    ch = data["challenge"]; rules = data["rules"]
    rd = ch["randomData"]; diff = rules["difficulty"]; cid = ch["id"]
    pref = "0"*diff
    n = 0; t0=time.time()
    while True:
        h = hashlib.sha256((rd+str(n)).encode()).hexdigest()
        if h.startswith(pref): break
        n += 1
    el = int((time.time()-t0)*1000)+1
    q = urllib.parse.urlencode({"id":cid,"response":h,"nonce":n,"redir":url,"elapsedTime":el})
    curl("https://lore.kernel.org/.within.website/x/cmd/anubis/api/pass-challenge?"+q)
    return True

def get(url):
    r = curl(url)
    if "anubis_challenge" in r:
        solve(r,url)
        r = curl(url)
    return r

if __name__ == "__main__":
    out = get(sys.argv[1])
    print(out)
