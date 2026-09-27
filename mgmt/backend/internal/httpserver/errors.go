package httpserver

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
)

// fail 统一错误响应 {code,message,request_id}，不携带原始配置或密码（§19.7）。
func fail(c *gin.Context, status int, code, msg string) {
	c.AbortWithStatusJSON(status, gin.H{
		"code":       code,
		"message":    msg,
		"request_id": c.GetString("request_id"),
	})
}

func fail401(c *gin.Context, msg string) { fail(c, http.StatusUnauthorized, "unauthorized", msg) }
func fail403(c *gin.Context, msg string) { fail(c, http.StatusForbidden, "forbidden", msg) }
func fail404(c *gin.Context, msg string) { fail(c, http.StatusNotFound, "not_found", msg) }
func fail409(c *gin.Context, msg string) { fail(c, http.StatusConflict, "conflict", msg) }
func fail422(c *gin.Context, msg string) { fail(c, http.StatusUnprocessableEntity, "invalid_param", msg) }
func fail429(c *gin.Context, msg string) { fail(c, http.StatusTooManyRequests, "rate_limited", msg) }
func fail500(c *gin.Context, msg string) { fail(c, http.StatusInternalServerError, "internal", msg) }

// pageArgs 解析 page/page_size（默认 20、最大 100）。
func pageArgs(c *gin.Context) (page, size int, offset int) {
	page, _ = strconv.Atoi(c.DefaultQuery("page", "1"))
	size, _ = strconv.Atoi(c.DefaultQuery("page_size", "20"))
	if page < 1 {
		page = 1
	}
	if size < 1 {
		size = 20
	}
	if size > 100 {
		size = 100
	}
	return page, size, (page - 1) * size
}

func pageResp(items any, total int64, page, size int) gin.H {
	return gin.H{"items": items, "total": total, "page": page, "page_size": size}
}

// BytesStr 64 位字节数以字符串序列化，避免 JS 安全整数失真（§19.5）。
type BytesStr int64

func (b BytesStr) MarshalJSON() ([]byte, error) {
	return []byte(`"` + strconv.FormatInt(int64(b), 10) + `"`), nil
}

func UnmarshalJSONBytesStr(raw []byte) (BytesStr, error) {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return 0, err
	}
	switch x := v.(type) {
	case float64:
		return BytesStr(int64(x)), nil
	case string:
		n, err := strconv.ParseInt(x, 10, 64)
		return BytesStr(n), err
	case nil:
		return 0, nil
	}
	return 0, nil
}
