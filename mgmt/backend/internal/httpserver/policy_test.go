package httpserver

import (
	"testing"
	"time"

	"routermgmt/internal/models"
)

func TestValidRebootCron(t *testing.T) {
	ok := []string{"", "0 0 * * *", "0 8 * * *", "0 */8 * * *", "0 */12 * * *"}
	for _, s := range ok {
		if !validRebootCron(s) {
			t.Errorf("should allow %q", s)
		}
	}
	bad := []string{"* * * * *", "0 0 * * * rm -rf /", ";reboot", "0 0 * * *\nreboot"}
	for _, s := range bad {
		if validRebootCron(s) {
			t.Errorf("should reject %q", s)
		}
	}
}

func TestValidKbps(t *testing.T) {
	if !validKbps(0) || !validKbps(1024) || !validKbps(1_000_000) {
		t.Fatal("legal kbps rejected")
	}
	if validKbps(-1) || validKbps(1_000_001) {
		t.Fatal("illegal kbps accepted")
	}
}

func TestDeviceServiceActive(t *testing.T) {
	now := time.Date(2026, 9, 20, 0, 0, 0, 0, time.UTC)
	past := now.Add(-time.Hour)
	future := now.Add(24 * time.Hour)

	dev := &models.Device{LicenseEnabled: true}
	if !deviceServiceActive(dev, now) {
		t.Fatal("no expiry should be active")
	}
	dev.LicenseExpiresAt = &future
	if !deviceServiceActive(dev, now) {
		t.Fatal("future expiry should be active")
	}
	dev.LicenseExpiresAt = &past
	if deviceServiceActive(dev, now) {
		t.Fatal("past expiry should be inactive")
	}
	dev.LicenseExpiresAt = nil
	dev.LicenseEnabled = false
	if deviceServiceActive(dev, now) {
		t.Fatal("manual disable should be inactive")
	}
	if deviceServiceActive(nil, now) {
		t.Fatal("nil device")
	}
}

func TestNormalizeAccessList(t *testing.T) {
	s, err := normalizeAccessList("192.168.1.10, 192.168.1.11\n04:D9:C8:69:BE:07")
	if err != nil {
		t.Fatal(err)
	}
	if s != "192.168.1.10,192.168.1.11,04:d9:c8:69:be:07" {
		t.Fatalf("got %q", s)
	}
	if _, err := normalizeAccessList("not-an-ip"); err == nil {
		t.Fatal("expected error")
	}
	if !validProxyMode("rule") || validProxyMode("script") {
		t.Fatal("proxy mode")
	}
	if !validAccessMode("whitelist") || validAccessMode("foo") {
		t.Fatal("access mode")
	}
}

func TestLicenseDaysLeft(t *testing.T) {
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	if licenseDaysLeft(nil, now) != nil {
		t.Fatal("nil device")
	}
	dev := &models.Device{}
	if licenseDaysLeft(dev, now) != nil {
		t.Fatal("permanent")
	}
	exp := now.Add(10*24*time.Hour + 2*time.Hour)
	dev.LicenseExpiresAt = &exp
	d := licenseDaysLeft(dev, now)
	if d == nil || *d != 10 {
		t.Fatalf("want 10 got %v", d)
	}
	past := now.Add(-3 * 24 * time.Hour)
	dev.LicenseExpiresAt = &past
	d = licenseDaysLeft(dev, now)
	if d == nil || *d != -3 {
		t.Fatalf("want -3 got %v", d)
	}
	soon := now.Add(6 * time.Hour)
	dev.LicenseExpiresAt = &soon
	d = licenseDaysLeft(dev, now)
	if d == nil || *d != 1 {
		t.Fatalf("under 24h should be 1, got %v", d)
	}
}
