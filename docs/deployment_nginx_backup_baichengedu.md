# backup.baichengedu.com 部署示例

本项目云端 API 和百度 OAuth 回调统一使用：

```text
https://backup.baichengedu.com
```

百度开放平台后台回调地址配置为：

```text
https://backup.baichengedu.com/v1/baidu/oauth/callback
```

## nginx 反代示例

```nginx
server {
    listen 443 ssl http2;
    server_name backup.baichengedu.com;

    ssl_certificate /etc/letsencrypt/live/backup.baichengedu.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/backup.baichengedu.com/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name backup.baichengedu.com;
    return 301 https://$host$request_uri;
}
```

## 服务端环境

`/etc/auto-backup-bdnetdesk/cloud-api.env` 至少需要包含：

```text
CLOUD_API_ADDR=127.0.0.1:8080
PUBLIC_BASE_URL=https://backup.baichengedu.com
BAIDU_APP_KEY=replace-with-baidu-app-key
BAIDU_APP_SECRET=replace-with-baidu-app-secret
BAIDU_SCOPE=basic,netdisk
BAIDU_REDIRECT_URI=https://backup.baichengedu.com/v1/baidu/oauth/callback
```

真实 PostgreSQL DSN、百度 App Secret 和任何 token 不得提交到仓库。

## RSA 备选模式

默认推荐客户端用密码派生 wrapping key，服务端只短暂接收 wrapping key 并立即加密百度 token，不保存用户密码或 key。

如需 RSA 模式，优先由客户端生成密钥对并只把公钥提交给服务端。部署端也提供备选脚本：

```powershell
.\scripts\generate_baidu_rsa_keypair.ps1
```

脚本输出到 `deploy-only/baidu-token-rsa/`，该目录已被 Git 忽略。该模式弱于客户端自持私钥，因为私钥如果留在服务器或项目部署目录中，会扩大泄露面。
