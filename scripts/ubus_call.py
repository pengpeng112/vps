"""ubus_call.py OBJECT METHOD [PARAMS_JSON] - always-fresh login helper."""
import json, subprocess, sys

def login():
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"call","params":["0"*32,"session","login",{"username":"root","password":"[REDACTED]"}]})
    out = subprocess.run(["curl","-s","--interface","192.168.2.105","--max-time","10","-H","Content-Type: application/json","-d",payload,"http://192.168.2.1/ubus"], capture_output=True, text=True).stdout
    return json.loads(out)["result"][1]["ubus_rpc_session"]

def call(obj, method, params):
    t = login()
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"call","params":[t,obj,method,params]})
    out = subprocess.run(["curl","-s","--interface","192.168.2.105","--max-time","15","-H","Content-Type: application/json","-d",payload,"http://192.168.2.1/ubus"], capture_output=True, text=True).stdout
    return json.loads(out).get("result")

if __name__ == "__main__":
    obj, method = sys.argv[1], sys.argv[2]
    params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(json.dumps(call(obj, method, params), ensure_ascii=False))
