package httpserver

import (
	"encoding/json"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"routermgmt/internal/models"
)

// audit 写 append-only 审计（运行态账号对 audit_log 只有 INSERT/SELECT，§19.4）。
func (s *Server) audit(c *gin.Context, actorType, actorID, action, target string, detail any) {
	var detailJSON = "{}"
	if detail != nil {
		if raw, err := json.Marshal(detail); err == nil {
			detailJSON = string(raw)
		}
	}
	ip := ""
	if c != nil {
		ip = clientIP(c)
	}
	entry := models.AuditLog{
		ActorType: actorType, ActorID: actorID, Action: action,
		Target: target, DetailJSON: detailJSON, IP: ip, TS: time.Now().UTC(),
	}
	if err := s.DB.Create(&entry).Error; err != nil {
		// 审计失败不阻断业务，但必须可见
		c.Header("X-Audit-Warn", "1")
	}
}

// auditTx 事务内写审计。
func auditTx(tx *gorm.DB, actorType, actorID, action, target string, detail any) error {
	var detailJSON = "{}"
	if detail != nil {
		if raw, err := json.Marshal(detail); err == nil {
			detailJSON = string(raw)
		}
	}
	return tx.Create(&models.AuditLog{
		ActorType: actorType, ActorID: actorID, Action: action,
		Target: target, DetailJSON: detailJSON, TS: time.Now().UTC(),
	}).Error
}
