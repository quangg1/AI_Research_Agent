# 🚨 FIX: "unsupported Unicode escape sequence" Error

## 🎯 NGUYÊN NHÂN

Lỗi này xảy ra khi Python gặp **backslash (`\`) không được escape đúng** trong strings, thường do:

1. **Windows paths** trong `.env` file
2. **JSON strings** có backslashes không escape
3. **Regex patterns** không dùng raw strings (`r"..."`)

---

## 🔍 CHẨN ĐOÁN NHANH

### Bước 1: Chạy script chẩn đoán

```bash
cd /workspace
python diagnose_unicode_error.py
```

Script này sẽ tự động kiểm tra:
- ✅ `.env` file
- ✅ JSON files
- ✅ Environment variables
- ✅ Common string parsing issues

### Bước 2: Kiểm tra `.env` file thủ công

```bash
# Windows - PowerShell
Get-Content .env | Select-String -Pattern ':\\'

# Linux/Mac
grep ':\\' .env
```

Nếu có kết quả → Đây là nguồn gốc lỗi!

---

## 🛠️ FIX THƯỜNG GẶP

### Fix 1: Windows Paths trong `.env`

#### ❌ SAI (Single backslash):
```env
CORPUS_UPLOAD_DIR=C:\Users\Admin\data\corpus
GOOGLE_API_KEY=sk-proj-123\456
```

**Lỗi:** Python đọc `\U` là Unicode escape sequence!

#### ✅ ĐÚNG (Option 1 - Double backslash):
```env
CORPUS_UPLOAD_DIR=C:\\Users\\Admin\\data\\corpus
GOOGLE_API_KEY=sk-proj-123\\456
```

#### ✅ ĐÚNG (Option 2 - Forward slash - RECOMMENDED):
```env
CORPUS_UPLOAD_DIR=C:/Users/Admin/data/corpus
GOOGLE_API_KEY=sk-proj-123/456
```

**Windows paths ACCEPT forward slashes in Python!**

### Fix 2: Environment Variables từ Shell

#### ❌ SAI:
```powershell
$env:CORPUS_UPLOAD_DIR = "C:\Users\Admin\data"
```

#### ✅ ĐÚNG:
```powershell
$env:CORPUS_UPLOAD_DIR = "C:/Users/Admin/data"
# Or
$env:CORPUS_UPLOAD_DIR = "C:\\Users\\Admin\\data"
```

### Fix 3: JSON Files

#### ❌ SAI:
```json
{
  "path": "C:\Users\Admin\data",
  "output": "D:\output\files"
}
```

#### ✅ ĐÚNG:
```json
{
  "path": "C:/Users/Admin/data",
  "output": "D:/output/files"
}
```

---

## 🔎 COMMON ESCAPE SEQUENCES CAUSING ERRORS

| Sequence | Intended | Python Reads As | Fix |
|----------|----------|-----------------|-----|
| `C:\Users` | Path | `\U` = Unicode | `C:/Users` or `C:\\Users` |
| `D:\test` | Path | `\t` = Tab | `D:/test` or `D:\\test` |
| `E:\new` | Path | `\n` = Newline | `E:/new` or `E:\\new` |
| `\result` | Path | `\r` = Carriage return | `/result` or `\\result` |
| `\folder` | Path | `\f` = Form feed | `/folder` or `\\folder` |
| `\backup` | Path | `\b` = Backspace | `/backup` or `\\backup` |

---

## 🎯 SPECIFIC FIXES PER FILE

### Fix `.env`

```bash
# Find problematic lines
cat .env | grep -n '\\'

# Common fixes:
# Before:
CORPUS_UPLOAD_DIR=../../data/corpus/orgs

# If you changed it to Windows path:
CORPUS_UPLOAD_DIR=D:\AI_Research_Agent\data\corpus\orgs  # ❌ WRONG

# Fix:
CORPUS_UPLOAD_DIR=D:/AI_Research_Agent/data/corpus/orgs  # ✅ RIGHT
```

### Fix Docker Compose Environment Variables

If you're passing paths via `docker-compose.yml`:

#### ❌ SAI:
```yaml
agent:
  environment:
    CORPUS_UPLOAD_DIR: D:\data\corpus
```

#### ✅ ĐÚNG:
```yaml
agent:
  environment:
    CORPUS_UPLOAD_DIR: D:/data/corpus
```

Or use relative paths:
```yaml
agent:
  environment:
    CORPUS_UPLOAD_DIR: ../../data/corpus/orgs
```

---

## 🚀 QUICK FIX COMMANDS

### PowerShell (Windows):

```powershell
# Backup your .env
Copy-Item .env .env.backup

# Fix backslashes in .env (replace \ with /)
(Get-Content .env) -replace '\\', '/' | Set-Content .env
```

### Bash (Linux/Mac):

```bash
# Backup your .env
cp .env .env.backup

# Fix backslashes in .env
sed -i 's/\\/\//g' .env
```

---

## 🧪 TEST YOUR FIX

### Test 1: Python can parse your .env

```python
python -c "
import os
from pathlib import Path

# Try to load .env manually
with open('.env', 'r') as f:
    for line in f:
        if '=' in line and not line.strip().startswith('#'):
            key, value = line.strip().split('=', 1)
            if value:
                try:
                    # Test if value can be parsed
                    eval(f'\"{value}\"')
                    print(f'✅ {key}: OK')
                except SyntaxError as e:
                    print(f'❌ {key}: FAIL - {e}')
"
```

### Test 2: Start agent without errors

```bash
# Rebuild with clean slate
docker-compose down -v
docker-compose up -d --build agent

# Check logs for Unicode errors
docker-compose logs agent | grep -i "unicode\|escape"
```

---

## 📊 TROUBLESHOOTING CHECKLIST

- [ ] Run `python diagnose_unicode_error.py`
- [ ] Check `.env` for Windows paths with `\`
- [ ] Replace all `C:\` with `C:/` or `C:\\`
- [ ] Check `docker-compose.yml` environment variables
- [ ] Verify no JSON files have unescaped backslashes
- [ ] Test with `docker-compose logs agent`
- [ ] If still failing, check NestJS API logs for errors passed from UI

---

## 🎯 IF ERROR PERSISTS

### Check where error originates:

```bash
# 1. Check agent logs
docker-compose logs agent --tail 200 > agent_logs.txt
grep -i "unicode\|escape\|error" agent_logs.txt -B 5 -A 5

# 2. Check API logs
docker-compose logs api --tail 200 > api_logs.txt
grep -i "unicode\|escape\|error" api_logs.txt -B 5 -A 5

# 3. Check web logs
docker-compose logs web --tail 20
```

### Look for:
- Stack trace showing which file/line caused the error
- Input that was being parsed when error occurred
- Environment variable values being used

---

## 💡 BEST PRACTICES

1. **ALWAYS use forward slashes** in .env files: `C:/Users/...`
2. **NEVER paste Windows paths** directly from File Explorer
3. **Use relative paths** when possible: `../../data/corpus`
4. **Validate .env** after editing: `python diagnose_unicode_error.py`
5. **Check Docker logs** after starting: `docker-compose logs agent --tail 50`

---

## 🆘 STILL STUCK?

If error persists after all fixes:

1. **Create minimal test case:**
   ```bash
   # Try running agent with minimal .env
   cp .env.example .env
   # Add only GOOGLE_API_KEY
   docker-compose up agent
   ```

2. **Check if error is in Python code:**
   ```bash
   # Search for problematic strings in agent code
   cd apps/agent
   python -m py_compile app/**/*.py
   ```

3. **Share full error stack trace:**
   - Include: File name, line number, exact error message
   - Check: `docker-compose logs agent | tail -50`

---

**✅ Sau khi fix, nhớ:**
```bash
docker-compose restart agent
# Or full rebuild:
docker-compose up -d --build agent
```
