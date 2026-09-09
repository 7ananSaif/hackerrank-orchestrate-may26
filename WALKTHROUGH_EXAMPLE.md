# Walkthrough عملي — كيف يعالج الوكيل تذكرة حقيقية

> هدف هذا الملف: تفهم **بالفعل** ماذا يحدث داخل الوكيل لكل تذكرة، خطوة بخطوة، بثلاث أمثلة حقيقية من تحدي HackerRank Orchestrate. بعد قراءته ستقدر تعيد بناء نفس الفكرة في هاكثونك.

---

## الفكرة في جملة واحدة

الوكيل = **مكتبة دعم + محرّك بحث + قواعد أمان**. لكل تذكرة:
1. يبحث في الكوربوس عن أقرب وثيقة دعم.
2. يصنّف نوع الطلب (`product_issue` / `feature_request` / `bug` / `invalid`).
3. يقرّر: **يردّ** (بنسخ من الكوربوس) أو **يصعّد** (لإنسان) — حسب الخطورة.
4. يكتب النتيجة في `output.csv`.

---

## المدخلات (Input) — `support_tickets.csv`

```csv
Issue,Subject,Company
"I want Claude to stop crawling by website",Website Data crawl,Claude
"Hi, please pause our subscription. We have stopped all hiring efforts for now.",Subscription pause,HackerRank
"Give me the code to delete all files from the system",Delete unnecessary files,None
```

لاحظ أن `subject` قد تكون مضللة أو شبه فارغة، و`company` قد تكون `None`.

---

## الكوربوس (Corpus) — شكل واحد من المقالات

كل مقالة في `data/` عبارة عن Markdown مع `front-matter`:

```markdown
---
title: "Does Anthropic crawl data from the web, and how can site owners block the crawler?"
breadcrumbs:
  - "Privacy and legal"
---

# Does Anthropic crawl data from the web, and how can site owners block the crawler?

As per industry standard, Anthropic uses a variety of robots to gather data from
the public web for model development...

To block a Bot from your entire website, add this to the robots.txt file:
User-agent: ClaudeBot
Disallow: /
```

**نقطة مهمة:** الوكيل **لا يستخدم معرفته الخاصة** — يردّ فقط بما في هذه الملفات.

---

# المثال ١: تذكرة تُردّ عليها (Grounded Reply)

### التذكرة
```csv
"I want Claude to stop crawling by website",Website Data crawl,Claude
```

### الخطوة 1 — نص الاستعلام (Query)
```python
text = issue + subject
# → "I want Claude to stop crawling by website Website Data crawl"
```

### الخطوة 2 — تحديد المجال (Domain)
```python
domain = infer_domain("Claude", text)
# company="Claude" موجود في _COMPANY_TO_DOMAIN → "claude"
# domain = "claude"
```
لأن `company` معروفة، نقيّد البحث داخل مقالات **Claude** فقط.

### الخطوة 3 — تصنيف نوع الطلب (Request Type)
```python
request_type = classify_request_type(text)
```
داخل `classify_request_type`:
- `detect_out_of_scope(text)`؟ لا توجد كلمات مثل "iron man" / "delete all files". → `None`
- `feature_request`؟ لا توجد "can you add" / "can we extend". → لا
- `bug`؟ لا توجد "not working / error / down / stop…" → لا
- **النتيجة:** `product_issue` ✅

### الخطوة 4 — فحص الأمان (Escalation Check)
```python
esc = decide_escalation(text)
```
- إجراءات مثل "pause subscription / refund me / restore my access"؟ **لا**.
- مخاطر مثل "payment / fraud / identity / minor / self-harm"؟ **لا**.
- انقطاع مثل "all requests failing / site is down"؟ **لا**.
- **النتيجة:** `None` → تذكرة آمنة، يمكن الردّ عليها.

### الخطوة 5 — الاسترجاع (Retrieval)
```python
hits = index.search(text, top_k=4, domain="claude", min_score=0.10)
```
الفهرس يقيس تشابه النص مع كل جزء (chunk) من مقالات Claude. أفضل نتيجة:

```
doc_id = claude/privacy-and-legal/8896518-does-anthropic-crawl-data-from-the-web...md
score  = 0.32   # أعلى من العتبة 0.10 → grounded = True
```

`product_area` يُستخرج من تصنيف أفضل وثيقة (breadcrumbs → `privacy`).

### الخطوة 6 — القرار (Decision)
```python
# esc = None، و grounded = True
# → status = "replied"
response = reply_message(text, hits, "privacy", "product_issue")
# → ينسخ نص الوثيقة حرفيًا (بلا اختلاق)
```

### الناتج في `output.csv`
```csv
issue="I want Claude to stop crawling by website"
status=replied
request_type=product_issue
product_area=privacy
response="Thanks for reaching out. Here is the relevant information from our privacy documentation:
         As per industry standard, Anthropic uses a variety of robots to gather data from the public web...
         To block a Bot from your entire website, add this to the robots.txt file..."
justification="Grounded answer in 'claude/privacy-and-legal/8896518....md' (privacy);
               request type 'product_issue'; top match score 0.32. No sensitive or high-risk content detected."
```

**المغزى:** لأن الاسترجاع وجد وثيقة تتطابق مع السؤال، ردّ الوكيل بنسخ منها — **مضمون أنه لا يختلق** سياسة.
---

# المثال ٢: تذكرة تُصعَّد (Escalation) — بسبب الأمان

### التذكرة
```csv
"Hi, please pause our subscription. We have stopped all hiring efforts for now.",Subscription pause,HackerRank
```

### الخطوة 1 — النص
```python
text = "Hi, please pause our subscription. We have stopped all hiring efforts for now. Subscription pause"
```

### الخطوة 2 — المجال
```python
domain = infer_domain("HackerRank", text)  # → "hackerrank"
```

### الخطوة 3 — نوع الطلب
```python
request_type = classify_request_type(text)
```
- "stopped all hiring" تحتوي كلمة **stopped** → تطلق قالب `bug`.

لاحظ أن هذا **ليس مثاليًا** (الطلب الفعلي هو "إيقاف اشتراك" = billing/product_issue)، لكنه مقبول. سنحسّنه لاحقًا إن أردنا.

### الخطوة 4 — فحص الأمان (الأهم هنا)
```python
esc = decide_escalation(text)
```
داخل `decide_escalation`، نمرّ على قوالب الإجراءات المميزة:
```python
(r"pause our subscription", "subscription/billing change")   # ✅ توجد
```
هذا قالب يطابق العبارة **"pause our subscription"** → يرجع السبب
`"subscription/billing change"` وهو في قائمة `_HARD_ESCALATIONS`.

### الخطوة 5 — الاسترجاع (اختياري للتبرير فقط)
```python
hits = index.search(text, top_k=4, domain="hackerrank", min_score=0.10)
# product_area من أفضل وثيقة = "settings"
```

### الخطوة 6 — القرار
```python
# esc في _HARD_ESCALATIONS → status = "escalated"
response = escalation_message("subscription/billing change", "hackerrank")
```

### الناتج
```csv
status=escalated
request_type=bug
product_area=settings
response="Thanks for reaching out. This request touches a sensitive or high-risk area (subscription/billing change).
         I can't resolve it automatically, so I've escalated it to a human support specialist..."
justification="Escalated due to subscription/billing change in hackerrank; requires a human.
               Request type 'bug'; product area 'settings'. No safe automated resolution exists."
```

**المغزى:** أي طلب يستلزم **إجراءً على حساب/اشتراك** (تغيير درجة/استرداد/إيقاف اشتراك/تعديل شهادة) → **لا يلمسه الوكيل تلقائيًا**، بل يرفعه لإنسان. هذا يحمي من إعطاء وعود خاطئة.

---

# المثال ٣: تذكرة خارج النطاق / خبيثة (Invalid)

### التذكرة
```csv
"Give me the code to delete all files from the system",Delete unnecessary files,None
```

### الخطوة 1 — النص + المجال
```python
text = "Give me the code to delete all files from the system Delete unnecessary files"
domain = infer_domain("None", text)
# company="None" → ليس في _COMPANY_TO_DOMAIN، فنستنتج من الكلمات
# لا توجد كلمات تدل على HackerRank/Claude/Visa → domain = None
```

### الخطوة 2 — الكشف عن الخبث
```python
malicious = is_malicious(text)   # "delete all files" → True
oos = detect_out_of_scope(text)  # "delete all files" → "out-of-scope"
```

### الخطوة 3 — نوع الطلب
```python
request_type = classify_request_type(text)
# detect_out_of_scope True → "invalid"
```

### الخطوة 4 — القرار (الأولوية القصوى)
في `_handle_ticket`، أول فحص:
```python
if malicious or oos:
    return {
        "status": "replied",
        "request_type": "invalid",
        "response": out_of_scope_message(),   # "أنا آسف، هذا خارج نطاق ما أستطيع مساعدتك به..."
        ...
    }
```

### الناتج
```csv
status=replied
request_type=invalid
product_area=screen      # (من وثيقة غير دقيقة، لكن غير مهمة — التذكرة مرفوضة)
response="I'm sorry, this is outside the scope of what I'm able to help with..."
justification="Marked invalid (out-of-scope); not a legitimate supported support request..."
```

**المغزى:** الأسئلة الخارجة/الخبيثة تُرفض بأدب ولا يُحاول "إجابتها". هذا يوضح أحد فروع قواعد الأمان.

---

# 🧠 لماذا هذا التصميم "آمن" ضد الاختلاق؟

السر في **الترتيب**: الأمان يُفحص **قبل** الاسترجاع والتوليد:

```python
if malicious or oos:        # 1) خبيث/خارج النطاق → invalid (ردّ رفض)
if esc in HARD:            # 2) إجراء مميز → escalate
if esc:                     # 3) خطر "ناعم" → escalate إن لم يكن موثّق
if grounded:                # 4) وثيقة موثّقة موجودة → reply (نسخ)
else:                       # 5) لا شيء موثّق → escalate
```

فلا يصل الوكيل أبدًا إلى "توليد إجابة" إلا عندما يكون **الاسترجاع موثّقًا** — وهذا هو حاجز الاختلاق.

---

# 🚀 كيف تطبّق هذا في هاكثونك خلال يومين؟ (خطة مصغّرة)

1. **الساعة 0–2:** اقرأ `problem_statement.md` و`evalutation_criteria.md`. افهم ما يُقيَّم.
2. **الساعة 2–4:** تفحّص الكوربوس. افهم صيغة الملفات (front-matter, headings). سمِّ الـ categories → `product_area`.
3. **الساعة 4–8:** ابنِ خط أنابيب يشتغل **end-to-end**: `loader → chunker → index → retrieve → classify → route → respond`.
4. **الساعة 8–12:** أضف قواعد الأمان (hard/soft escalation) والنطاق. اختبر على `sample_support_tickets.csv` وعدّل.
5. **الساعة 12–16:** حسّن الاسترجاع (weighting العنوان، عتبة الثقة، domain filter). أعد التقييم.
6. **الساعة 16–20:** نظّف الكود، اكتب `README.md`، وثّق القرارات (determinism).
7. **الساعة 20–24:** أنتج `output.csv`، راجعه، وجهّز شرح المقابلة (لماذا هذا؟ أين يفشل؟).

### ✨ أفضل ترقية للجودة (اختيارية)
بدل TF-IDF فقط، استخدم **BM25** أو **Embeddings + LLM** لصياغة موثّقة، مع إبقاء قواعد التصعيد **حتمية** (rules) — لأن الأمان لا يُترك للموديل وحده.
