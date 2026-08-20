# Deploy Kiln on Oracle Always Free (`AUTH_MODE=dev`)

Mục tiêu: full stack (web + API + agent + Postgres + Redis + Qdrant) trên **một VM Oracle**, HTTPS, domain `.io.vn`, Gemini free, auth dev.

Cursor **không đăng ký hộ** Oracle / domain / Google. Làm 3 tài khoản dưới đây, rồi SSH vào VM chạy lệnh.

`AUTH_MODE=dev` trên internet: ai biết gửi header `X-Dev-User-Id` / `X-Dev-Org-Id` đều vào được app. Chỉ dùng demo / tự chơi, không phải production có user thật.

## 0. Đẩy code lên GitHub

Máy bạn có thay đổi local. Trên VM sẽ `git clone` remote. Commit + push `main` trước (kể cả `docker-compose.oracle.yml` và file này).

Repo: https://github.com/quangg1/AI_Research_Agent

## 1. Gemini (free)

1. Mở [Google AI Studio](https://aistudio.google.com/apikey) → Create API key.
2. Giữ key; dán vào `.env` trên VM (`GOOGLE_API_KEY=`).

Không bắt buộc Tavily: agent dùng DuckDuckGo nếu trống `TAVILY_API_KEY`.

## 2. Domain `.io.vn`

1. Đăng ký tại nhà đăng ký `.vn` (PA Vietnam, Mắt Bão, Nhân Hòa, …) — tên dạng `something.io.vn`.
2. **Chưa** trỏ DNS cho đến khi có IP public của VM (bước 3).
3. Email Let's Encrypt: dùng email thật (`ACME_EMAIL`).

## 3. Oracle Always Free VM

1. [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) → Sign up (thẻ để xác minh; Always Free không trừ nếu không nâng PAYG).
2. Compute → Create instance:
   - Image: **Ubuntu 22.04/24.04**
   - Shape: **VM.Standard.A1.Flex** — **2 OCPU / 12 GB** (đúng hạn Always Free hiện tại)
   - Networking: gán **public IP**
   - Lưu file SSH key
3. **Security list / NSG** của subnet: Ingress **TCP 22, 80, 443** từ `0.0.0.0/0`. Egress all.
4. Ghi **public IP**.
5. DNS domain: record **A** `@` (và `www` nếu dùng) → IP đó. Chờ propagate (vài phút đến vài giờ).

Đăng nhập:

```bash
ssh -i /path/to/oracle-key ubuntu@THE_PUBLIC_IP
```

## 4. Cài Docker trên VM

```bash
git clone https://github.com/quangg1/AI_Research_Agent.git
cd AI_Research_Agent
chmod +x infra/oracle/bootstrap.sh
./infra/oracle/bootstrap.sh
```

Nếu script bảo log out: `exit`, SSH lại, `cd AI_Research_Agent`.

## 5. File `.env`

```bash
cp .env.oracle.example .env
nano .env
```

Đổi:

| Biến | Giá trị |
| --- | --- |
| `DOMAIN` | `yourname.io.vn` (không `https://`) |
| `ACME_EMAIL` | email của bạn |
| `CORS_ORIGINS` | `https://yourname.io.vn` |
| `GOOGLE_API_KEY` | key Gemini |
| `AGENT_SHARED_KEY` | chạy `openssl rand -hex 32` rồi dán; copy cùng giá trị sang `API_TO_AGENT_KEY` |

`VITE_API_URL` để **trống** (UI gọi `/v1` cùng domain).

`AUTH_MODE=dev` và `ALLOW_DEV_AUTH=true` đã sẵn trong template.

## 6. Chạy stack

```bash
docker compose -f docker-compose.yml -f docker-compose.oracle.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.oracle.yml ps
docker compose -f docker-compose.yml -f docker-compose.oracle.yml logs -f caddy web api agent
```

Lần đầu Let's Encrypt cần DNS đã trỏ đúng. Lỗi chứng chỉ: kiểm tra `A` record, security list 80/443, `DOMAIN` khớp.

Mở `https://yourname.io.vn` — UI Research, auth dev (header mặc định `user_dev` / `org_default`).

## 7. Bảo trì

```bash
# cập nhật code
git pull
docker compose -f docker-compose.yml -f docker-compose.oracle.yml up -d --build

# backup DB (chạy trên VM)
docker compose exec -T postgres pg_dump -U kiln kiln > backup-$(date +%Y%m%d).sql
```

Máy Oracle **không tắt**; Always Free không sleep như Render Free.

## Troubleshooting

| Hiện tượng | Việc làm |
| --- | --- |
| `AUTH_MODE=dev is not allowed in production` | `.env` thiếu `ALLOW_DEV_AUTH=true` |
| Agent 401 | `AGENT_SHARED_KEY` trống hoặc khác `API_TO_AGENT_KEY` |
| Caddy không lấy cert | DNS chưa về IP VM, hoặc port 80 bị chặn |
| Out of memory | `docker stats`; 12 GB thường đủ, đừng chạy thêm VM trên cùng tenancy |
| Shape A1 hết hàng | Thử region/AD khác, hoặc tạo lại sau |
