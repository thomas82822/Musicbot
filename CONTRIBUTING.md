# 🤝 Contributing Guide

4ST Music Bot mein contribute karne ke liye shukriya! Ye guide padho pehle.

---

## 🐛 Bug Report Karna

1. Pehle check karo ki same issue pehle se exist toh nahi karta [issues](https://github.com/thomas91929/Musicbot/issues)
2. **[Bug Report template](https://github.com/thomas91929/Musicbot/issues/new?template=bug_report.md)** use karo
3. Error logs + steps to reproduce zaroor include karo

---

## 💡 Feature Request

1. [Feature Request template](https://github.com/thomas91929/Musicbot/issues/new?template=feature_request.md) use karo
2. Feature ka use case clearly explain karo

---

## 🔧 Code Contribute Karna

### Setup

```bash
# 1. Repo fork karo (GitHub pe "Fork" button)

# 2. Apna fork clone karo
git clone https://github.com/TERA_USERNAME/Musicbot
cd Musicbot

# 3. Dependencies install karo
pip install -r requirements.txt

# 4. Feature branch banao
git checkout -b feature/teri-feature-ka-naam
```

### Coding Guidelines

- **Python 3.10+** syntax use karo (match-case, type hints, etc.)
- Har handler mein `callback.answer()` **pehli line** mein hona chahiye (speed fix)
- Bot name / support link `config.py` se hardcode mat karo — `helpers/branding.py` use karo
- Log messages English mein, user-facing messages Hindi/Hinglish mein rakh sakte ho
- Naye plugins `plugins/` folder mein daalo, helpers `helpers/` mein

### Commit Format

```
feat: naya feature add kiya
fix: bug fix
refactor: code improve kiya (no feature change)
docs: documentation update
chore: dependencies / config
```

### Pull Request

```bash
# Changes commit karo
git add .
git commit -m "feat: teri feature ka description"

# Push karo
git push origin feature/teri-feature-ka-naam
```

Phir GitHub pe "Compare & pull request" karo.

---

## 📋 PR Checklist

PR submit karne se pehle check karo:

- [ ] Code locally test kiya
- [ ] Koi secrets / tokens commit mein nahi hain
- [ ] Existing code style follow kiya
- [ ] `callback.answer()` sab callbacks mein pehle hai
- [ ] Hardcoded bot name/link nahi hai (branding system use kiya)

---

Thank you! 🙏
