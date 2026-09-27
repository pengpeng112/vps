"""Deploy the direct-mihomo runtime files to the router via quickfile API."""
import json, subprocess, sys, urllib.parse

BASE = "http://192.168.1.1"
COOKIE = "/tmp/lc_r2.txt"

def qf(api, params):
    body = urllib.parse.urlencode(params)
    out = subprocess.run(
        ["curl", "-s", "--interface", "192.168.1.105", "--max-time", "60",
         "-b", COOKIE, "-X", "POST",
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "-d", body, f"{BASE}/cgi-bin/luci/quickfile/api/{api}"],
        capture_output=True, text=True).stdout
    return out.strip()

def writefile(directory, name, content):
    r = qf("writefile", {"dir": directory, "name": name, "content": content})
    print(f"writefile {directory}/{name}: {r[:100]}")
    return r

def chmod(directory, name, mode):
    r = qf("chmod", {"dir": directory, "name": name, "mode": mode})
    print(f"chmod {name} {mode}: {r[:80]}")

# 1. runtime config
writefile("/etc/mihomo", "run.yaml", open("run.yaml", encoding="utf-8").read())

# 2. init script
writefile("/etc/init.d", "mihomo", open("mihomo_init.sh", encoding="utf-8").read())
chmod("/etc/init.d", "mihomo", "0755")

# 3. firewall include script
writefile("/etc", "mihomo-fw.sh", open("mihomo_fw.sh", encoding="utf-8").read())
chmod("/etc", "mihomo-fw.sh", "0755")

print("DONE")
