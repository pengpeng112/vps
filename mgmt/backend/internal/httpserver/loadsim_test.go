package httpserver

// loadsim_test.go — 量产负载模拟（统一优化 M09/P08，2026-09-27）。
// 隔离环境专用：需 ROUTERMGMT_TEST_DSN（隔离库）且 ROUTERMGMT_LOADSIM=1 才运行，
// 普通单测/CI 不会触发。全部设备/凭据为虚构（loadsim-*），不触真实路由器。
// 模拟规模：50 设备 × 20 轮（等效 30s 心跳周期 × 20 ≈ 10 分钟，压缩时间线），
// 每轮：心跳(带唯一 sample_id) + 领任务 + 对 5 台随机设备灌 ping_test 并完成。
// 记录：各请求 P50/P95/P99、错误率、任务/指标行数、进程内存。

import (
	"fmt"
	"math/rand"
	"os"
	"sort"
	"testing"
	"time"

	"gorm.io/gorm"

	"routermgmt/internal/models"
)

func TestLoadSim50Devices(t *testing.T) {
	if os.Getenv("ROUTERMGMT_LOADSIM") == "" {
		t.Skip("需 ROUTERMGMT_LOADSIM=1（隔离负载模拟专用）")
	}
	requireDB(t)

	const nDev = 50
	const rounds = 20
	jwt := ensureAdmin(t)

	creds := make([]deviceCreds, 0, nDev)
	for i := 0; i < nDev; i++ {
		c := createDeviceAndEnroll(t, jwt, fmt.Sprintf("loadsim-dev-%02d", i))
		creds = append(creds, c)
	}
	t.Cleanup(func() {
		for _, c := range creds {
			srv.DB.Delete(&models.Device{}, "id=?", c.ID)
			srv.DB.Exec("DELETE FROM tasks WHERE device_id=?", c.ID)
			srv.DB.Exec("DELETE FROM metrics WHERE device_id=?", c.ID)
		}
	})

	var hbLat, tgLat, rsLat []float64
	var errs int
	var total int
	rng := rand.New(rand.NewSource(42))
	round := func(r int) {
		// 每轮给 5 台随机设备灌一个 ping_test（虚构目标 203.0.113.9，TEST-NET-3）
		victims := rng.Perm(nDev)[:5]
		for _, vi := range victims {
			c := creds[vi]
			_ = srv.DB.Transaction(func(tx *gorm.DB) error {
				_, err := srv.createTask(tx, c.ID, "ping_test", map[string]any{"host": "203.0.113.9"},
					nil, "loadsim", fmt.Sprintf("ls-%02d-r%02d", vi, r), 120*time.Second)
				return err
			})
		}
		for i, c := range creds {
			body := mustJSON(t, map[string]any{
				"device_id": c.ID, "ts": time.Now().Unix(),
				"metrics": map[string]any{
					"sample_id": fmt.Sprintf("ls-%02d-r%02d", i, r),
					"cpu":       float64(rng.Intn(80)) / 100.0,
					"mem_used_mb": 100 + rng.Intn(400), "mem_total_mb": 1024,
					"uptime_s": 60 * (r + 1), "config_version": 0,
				},
			})
			t0 := time.Now()
			st, _, _ := agentRequest(t, c, "POST", "/api/v1/agent/heartbeat", body, nil)
			d := time.Since(t0).Seconds()
			total++
			hbLat = append(hbLat, d)
			if st != 200 {
				errs++
				continue
			}
			t0 = time.Now()
			st, resp, _ := agentRequest(t, c, "GET", "/api/v1/agent/tasks", nil, nil)
			d = time.Since(t0).Seconds()
			total++
			tgLat = append(tgLat, d)
			if st != 200 {
				errs++
				continue
			}
			if tasks, ok := resp["tasks"].([]any); ok && len(tasks) > 0 {
				for _, raw := range tasks {
					task := raw.(map[string]any)
					tid, _ := task["task_id"].(string)
					lease, _ := task["lease_id"].(string)
					t0 = time.Now()
					st2, _, _ := agentRequest(t, c, "POST", "/api/v1/agent/tasks/"+tid+"/result",
						mustJSON(t, map[string]any{"lease_id": lease, "status": "ok", "detail": map[string]any{"sim": true}}), nil)
					d = time.Since(t0).Seconds()
					total++
					rsLat = append(rsLat, d)
					if st2 != 200 {
						errs++
					}
				}
			}
		}
	}

	for r := 0; r < rounds; r++ {
		round(r)
	}

	pct := func(ds []float64, p float64) float64 {
		if len(ds) == 0 {
			return -1
		}
		sort.Float64s(ds)
		return ds[int(float64(len(ds)-1)*p)]
	}
	var nTasks, nMetrics, nNonces int64
	srv.DB.Model(&models.Task{}).Count(&nTasks)
	srv.DB.Model(&models.Metric{}).Count(&nMetrics)
	srv.DB.Raw("SELECT count(*) FROM used_nonces").Scan(&nNonces)
	fmt.Printf("[loadsim] 请求总数=%d 错误=%d 错误率=%.3f%%\n", total, errs, float64(errs)/float64(total)*100)
	fmt.Printf("[loadsim] 心跳 n=%d P50=%.1fms P95=%.1fms P99=%.1fms\n", len(hbLat), pct(hbLat, .5)*1000, pct(hbLat, .95)*1000, pct(hbLat, .99)*1000)
	fmt.Printf("[loadsim] 领任务 n=%d P50=%.1fms P95=%.1fms P99=%.1fms\n", len(tgLat), pct(tgLat, .5)*1000, pct(tgLat, .95)*1000, pct(tgLat, .99)*1000)
	if len(rsLat) > 0 {
		fmt.Printf("[loadsim] 回结果 n=%d P50=%.1fms P95=%.1fms\n", len(rsLat), pct(rsLat, .5)*1000, pct(rsLat, .95)*1000)
	}
	fmt.Printf("[loadsim] DB 行数: tasks=%d metrics=%d used_nonces=%d\n", nTasks, nMetrics, nNonces)
	if total > 0 && errs*100/total > 1 {
		t.Fatalf("错误率超 1%%: %d/%d", errs, total)
	}
	if pct(hbLat, .95) > 0.5 {
		t.Fatalf("心跳 P95 超 500ms: %.1fms", pct(hbLat, .95)*1000)
	}
}
