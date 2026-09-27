# golang:1.26-alpine 与本机 go1.26.1 对齐；CGO 关闭出静态单二进制
FROM golang:1.26-alpine AS build
WORKDIR /src
ENV GOFLAGS=-mod=mod GOPROXY=https://goproxy.cn,direct
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/server ./cmd/server \
 && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/provision ./cmd/provision

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
COPY --from=build /out/server /usr/local/bin/routermgmt-server
COPY --from=build /out/provision /usr/local/bin/routermgmt-provision
# 容器内监听 0.0.0.0:8443，仅容器网络访问；不发布宿主机端口（§11.1）
ENV LISTEN=0.0.0.0:8443 DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8443
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8443/api/v1/health || exit 1
ENTRYPOINT ["routermgmt-server"]
