// provision：受信任电脑上代设备调用 enroll 并落盘离线引导包（§19.1）。
// 引导包权限 600；不在终端打印秘密；不写入代码目录。
//
// 用法：
//   provision -server http://127.0.0.1:8443 -enroll-code <code> -out /tmp/pkg-rtr-xxx.json
//   provision -inspect /tmp/pkg-rtr-xxx.json   # 只显示非秘密字段
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

func main() {
	server := flag.String("server", "", "管理 API 基址（Session 1 http://127.0.0.1:8443；生产走 WG HTTPS）")
	enrollCode := flag.String("enroll-code", "", "一次性注册码")
	hwModel := flag.String("hw-model", "", "硬件型号（可选）")
	firmware := flag.String("firmware", "", "固件版本（可选）")
	out := flag.String("out", "", "离线引导包输出路径")
	inspect := flag.String("inspect", "", "查看引导包非秘密字段")
	flag.Parse()

	if *inspect != "" {
		inspectPackage(*inspect)
		return
	}
	if *server == "" || *enrollCode == "" || *out == "" {
		flag.Usage()
		os.Exit(2)
	}
	baseURL := normalizeServer(*server)

	body, _ := json.Marshal(map[string]string{
		"enroll_code":  *enrollCode,
		"hw_model":     *hwModel,
		"firmware_ver": *firmware,
	})
	req, _ := http.NewRequest(http.MethodPost, baseURL+"/api/v1/devices/enroll", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		fatal("请求 enroll 失败: %v", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		fatal("enroll 失败（%d）: %s", resp.StatusCode, strings.TrimSpace(string(raw)))
	}
	if err := os.WriteFile(*out, raw, 0o600); err != nil {
		fatal("写引导包失败: %v", err)
	}
	var pkg map[string]any
	json.Unmarshal(raw, &pkg)
	// 只打印非秘密摘要
	fmt.Printf("引导包已写入 %s（权限 600）\n", *out)
	fmt.Printf("  device_id=%v transport_mode=%v\n", pkg["device_id"], pkg["transport_mode"])
	fmt.Printf("  sub_url 与凭据在包内；用 -inspect 查看非秘密字段\n")
	fmt.Printf("  通过可信 LAN 的 SSH/本地介质把包交给 install-agent.sh；用完清理临时副本\n")
}

func inspectPackage(path string) {
	raw, err := os.ReadFile(path)
	if err != nil {
		fatal("读引导包失败: %v", err)
	}
	var pkg map[string]any
	if err := json.Unmarshal(raw, &pkg); err != nil {
		fatal("解析失败: %v", err)
	}
	wg, _ := pkg["wg"].(map[string]any)
	fmt.Printf("device_id=%v transport_mode=%v\n", pkg["device_id"], pkg["transport_mode"])
	fmt.Printf("wg.address=%v wg.endpoint=%v wg.allowed_ips=%v\n", wg["address"], wg["endpoint"], wg["allowed_ips"])
	fmt.Printf("sign_public_key 前 40 字符=%v...\n", substring(fmt.Sprint(pkg["sign_public_key"]), 40))
	fmt.Println("（hmac_key / sub_token / wg.private_key 不显示）")
}

func substring(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func normalizeServer(u string) string {
	return strings.TrimRight(u, "/")
}

func fatal(f string, args ...any) {
	fmt.Fprintf(os.Stderr, "provision: "+f+"\n", args...)
	os.Exit(1)
}
