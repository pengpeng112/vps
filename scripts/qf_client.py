"""Reliable quickfile client: fresh LuCI login before every call."""
import json, subprocess, urllib.parse

COOKIE = "/tmp/lc_qf.txt"
IFACE = "192.168.2.105"

def _curl(args, timeout="60"):
    return subprocess.run(
        ["curl", "-s", "--interface", IFACE, "--max-time", timeout] + args,
        capture_output=True, text=True).stdout

def relogin():
    subprocess.run(
        ["curl", "-s", "--interface", IFACE, "--max-time", "8", "-c", COOKIE,
         "-o", "/dev/null",
         "--data-urlencode", "luci_username=root",
         "--data-urlencode", "luci_password=[REDACTED]",
         "http://192.168.2.1/cgi-bin/luci/"],
        capture_output=True, text=True)

def qf(api, params, timeout="60"):
    relogin()
    body = urllib.parse.urlencode(params)
    out = _curl(["-b", COOKIE, "-X", "POST",
                 "-H", "Content-Type: application/x-www-form-urlencoded",
                 "-d", body,
                 f"http://192.168.2.1/cgi-bin/luci/quickfile/api/{api}"], timeout)
    return out.strip()

def readfile(directory, name):
    relogin()
    return _curl(["-b", COOKIE,
                  f"http://192.168.2.1/cgi-bin/luci/quickfile/api/readfile?dir={urllib.parse.quote(directory)}&name={urllib.parse.quote(name)}"])

def writefile(directory, name, content):
    return qf("writefile", {"dir": directory, "name": name, "content": content}, timeout="120")

def ubus_rc(name, action):
    return _curl(["-X", "POST", "-H", "Content-Type: application/json",
                  "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "call", "params":
                                    ["0" * 32, "session", "login", {"username": "root", "password": "password"}]}),
                  "http://192.168.2.1/ubus"])  # placeholder

if __name__ == "__main__":
    import sys
    print(readfile("/tmp", "mihomo.log")[:200])
