// Package main — qos-gate: 路由器本机轻量下行令牌桶（governance-20261119/02 Q3 唯一路线）。
// bucket.go: 每类一个聚合共享下行桶。只限下行；无令牌时 Wait（释放锁）阻塞读上游——
// TCP 接收窗口自然收缩，反压真实上游少发（Q3.3 省流的关键，禁止大 buf 预读缓存慢发）。
// 紧急解除：rate 归零（无限）+ Broadcast 立即唤醒全部等待者（Q4.3/Q4.9）。
package main

import (
	"context"
	"errors"
	"sync"
	"time"
)

// Bucket 聚合令牌桶。rateBps=0 表示未启用（直通）；Emergency=true 表示紧急解除（全部直通）。
// 20ms ticker 驱动 refill+Broadcast：无令牌等待者按时间片唤醒（无外部事件也能推进）。
type Bucket struct {
	mu        sync.Mutex
	cond      *sync.Cond
	name      string
	rateBps   float64 // 下行字节/秒；<=0 视为不限
	burst     float64 // 令牌上限（突发）
	tokens    float64
	last      time.Time
	emergency bool
	total     float64 // 已发放字节（分类计量证据）
	waiting   int
	stop      chan struct{}
	stopOnce  sync.Once
	stopped   bool
}

const refillInterval = 20 * time.Millisecond

func NewBucket(name string, rateBps int64, burstBytes int64) *Bucket {
	b := &Bucket{name: name, last: time.Now(), stop: make(chan struct{})}
	b.cond = sync.NewCond(&b.mu)
	b.setLocked(float64(rateBps), float64(burstBytes))
	go b.tickLoop()
	return b
}

func (b *Bucket) tickLoop() {
	t := time.NewTicker(refillInterval)
	defer t.Stop()
	for {
		select {
		case <-b.stop:
			return
		case <-t.C:
			b.mu.Lock()
			hadWaiting := b.waiting > 0
			b.refillLocked()
			if hadWaiting {
				b.cond.Broadcast()
			}
			b.mu.Unlock()
		}
	}
}

// Stop 停止 ticker（测试/关停）。
func (b *Bucket) Stop() {
	b.stopOnce.Do(func() { b.mu.Lock(); b.stopped = true; close(b.stop); b.cond.Broadcast(); b.mu.Unlock() })
}

func (b *Bucket) setLocked(rateBpsBits, burstBytes float64) {
	// Q0.1 口径：down_bps=bit/s（60000→7500B/s）。桶内部一律按字节/秒运算。
	rate := rateBpsBits / 8
	if burstBytes <= 0 {
		burstBytes = rate / 2 // 默认 0.5s 突发
	}
	if burstBytes < 1 {
		burstBytes = 1
	}
	b.rateBps = rate
	b.burst = burstBytes
	if b.tokens > burstBytes {
		b.tokens = burstBytes
	}
}

// SetRate 热更新速率（reload/在线调参）。
func (b *Bucket) SetRate(rateBps, burstBytes int64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.refillLocked()
	b.setLocked(float64(rateBps), float64(burstBytes))
	b.cond.Broadcast()
}

// EmergencyRelease 紧急解除：桶立即直通并唤醒全部阻塞者。调用方负责先持久化（Q4.3 顺序）。
func (b *Bucket) EmergencyRelease() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.emergency = true
	b.cond.Broadcast()
}

// Resume 恢复限速（clear-emergency / 新代际）。rateBps<=0 视为直通。
func (b *Bucket) Resume(rateBps, burstBytes int64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.emergency = false
	b.refillLocked()
	b.setLocked(float64(rateBps), float64(burstBytes))
	b.cond.Broadcast()
}

func (b *Bucket) refillLocked() {
	now := time.Now()
	if b.rateBps > 0 && !b.last.IsZero() {
		b.tokens += now.Sub(b.last).Seconds() * b.rateBps
		if b.tokens > b.burst {
			b.tokens = b.burst
		}
	}
	b.last = now
}

// unlimitedLocked 桶当前是否直通。
func (b *Bucket) unlimitedLocked() bool {
	return b.emergency || b.rateBps <= 0
}

// Take 阻塞直到取得令牌，返回实际授权字节数（调用方必须按返回值限制本次 Read 长度，
// 否则限速会被放大 n/burst 倍——2026-11-19 PoC 实测修复）。直通态返回原 n。
func (b *Bucket) Take(n int) int {
	n, _ = b.TakeContext(context.Background(), n)
	return n
}

func (b *Bucket) TakeContext(ctx context.Context, n int) (int, error) {
	if n <= 0 {
		n = 1
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	stopWake := context.AfterFunc(ctx, func() { b.mu.Lock(); b.cond.Broadcast(); b.mu.Unlock() })
	defer stopWake()
	for {
		if err := ctx.Err(); err != nil {
			return 0, err
		}
		if b.stopped {
			return 0, errors.New("bucket stopped")
		}
		if b.unlimitedLocked() {
			b.total += float64(n)
			return n, nil
		}
		b.refillLocked()
		want := float64(n)
		if want > b.burst {
			want = b.burst
		}
		if want < 1 {
			want = 1
		}
		if b.tokens >= want {
			b.tokens -= want
			b.total += want
			return int(want), nil
		}
		b.waiting++
		b.cond.Wait()
		b.waiting--
	}
}

// Refund returns an unused reservation after short reads and EOF.
func (b *Bucket) Refund(n int) {
	if n <= 0 {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.tokens += float64(n)
	if b.tokens > b.burst {
		b.tokens = b.burst
	}
	b.total -= float64(n)
	b.cond.Broadcast()
}

// consumeActual 记账钩子：Take(n) 为预授权上限，实际读 m<n 时差额随下一 refill 粒度吸收。
// 4KB chunk 相对 7500B/s 桶半秒粒度，限速精度影响有界（PoC 口径声明）。
func (b *Bucket) consumeActual(m int) {
	_ = m
}

// Stats 控制口观测。
func (b *Bucket) Stats() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	return map[string]any{
		"name":                  b.name,
		"rate_bps":              b.rateBps,
		"down_bps":              b.rateBps * 8,
		"rate_bytes_per_second": b.rateBps,
		"burst":                 b.burst,
		"tokens":                b.tokens,
		"total":                 b.total,
		"emergency":             b.emergency,
		"waiting":               b.waiting,
		"unlimited":             b.unlimitedLocked(),
	}
}
