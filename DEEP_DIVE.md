# الشرح العميق (Deep Dive) — كيف يعمل الوكيل من الداخل

> هذا الملف يشرح **كل مكوّن** في المشروع بالتفصيل الدقيق: الخوارزميات، والمعادلات، والكود سطرًا بسطر. اقرأه بتركيز — ستقدر منه أن تشرح أي سؤال في مقابلة الـ AI Judge، وأن تعيد بناء النظام في هاكثونك بعد يومين.

---

## 0) خريطة البيانات الكاملة (Data Flow)

```
support_tickets.csv
   │  (لكل صف: issue, subject, company)
   ▼
[_handle_ticket]
   │
   ├─► text = issue + " " + subject            # دمج النص
   ├─► domain = infer_domain(company, text)    # claude/hackerrank/visa/None
   ├─► request_type = classify_request_type()  # product_issue/feat/bug/invalid
   ├─► esc = decide_escalation(text)           # سبب التصعيد أو None
   ├─► oos / malicious / vague / howto         # أعلام إضافية
   ├─► hits = index.search(text, domain)       # أقرب الوثائق
   │
   ▼
[شجرة القرار]  ──► status + response + justification + product_area
   │
   ▼
output.csv (issue, subject, company, response, product_area, status, request_type, justification)
```

النقطة الأهم: **شجرة القرار تفحص الأمان أولًا، والاسترجاع في مكانه، ثم تحدّد الناتج.**

---

## 1) `corpus_engine.py` — قلب النظام (Corpus + Retrieval)

### 1.1 قراءة كل المقالات
```python
for path in sorted(corpus_dir.rglob("*.md")):
    raw = path.read_text(encoding="utf-8", errors="ignore")
```
- `rglob("*.md")` = **بحث جميع** ملفات markdown في كل المجلدات الفرعية recursively.
- `sorted(...)` = ترتيب ثابت (deterministic) حتى تكون النتائج قابلة للتكرار.
- `errors="ignore"` = إذا وُجد ترميز غريب، لا يتعطل البرنامج (تحمّل أخطاء).

### 1.2 تحليل الـ Front-matter
المقال يبدأ بـ:
```
---
title: "..."
breadcrumbs:
  - "Privacy and legal"
---
```
الكود يفصلها بمطابقة regex: `^---\s*\n(.*?)\n---\s*\n` ثم يقرأ:
```python
meta["title"]        = "..."
meta["breadcrumbs"]  = ["Privacy and legal"]   # قائمة
```
**لماذا مهم؟** `title` يُستخدم لترجيح البحث (لأنه يعيد صياغة السؤال)، و`breadcrumbs` يُستخدم لاستخراج `product_area`.

### 1.3 تنظيف الـ Markdown (`_clean_line`)
المقالات تحتوي روابط صور وروابط ومصادر — كلها ضوضاء تُزيل قبل الفهرسة:
```python
line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)      # حذف الصور
line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # [نص](رابط) → نص فقط
line = re.sub(r"https?://\S+", "", line)              # حذف الروابط الخام
line = re.sub(r"^#{1,6}\s*", "", line)                # حذف علامات العناوين
line = re.sub(r"^\s*[-*+]\s+", "", line)              # حذف نقاط القوائم
```
**لماذا؟** وجود URLs في الفهرس يشوّش التشابه. نظافة النص = دقة استرجاع أعلى.

### 1.4 التقطيع حسب العناوين (`_chunk_body`)
نقسّم المقال عند كل عنوان markdown (`#`, `##`, ...):
```python
for raw_line in body.splitlines():
    m = _HEADING_RE.match(raw_line)     # يبدأ بـ #
    if m:
        flush()                          # احفظ القسم السابق
        current_heading = _clean_line(raw_line)   # عنوان جديد
    else:
        current_lines.append(_clean_line(raw_line))
```
النتيجة: **مقطع (chunk) لكل قسم** = (عنوان، نصه).

**لماذا التقطيع مهم؟** المقال قد يكون 3000 كلمة ويغطي 6 مواضيع. لو فهرسناه كوحدة واحدة، تصبح كل التذاكر متشابهة معه. التقطيع يجعل كل سؤال يقابل **القسم الصحيح فقط** → دقة أعلى بكثير.

### 1.5 بناء الفهرس TF-IDF (`_build`)

**الفكرة:** لكل مقطع، نبني "بصمة" رقمية من كلماته، ثم نقارن بصمة السؤال ببصمة كل مقطع.

### 1.5.1 الـ Tokenization (`tokenize`)
دالة `tokenize` تفعل ثلاث خطوات:
1. **تقسيم** النص إلى كلمات: `re.findall(r"[A-Za-z0-9]+", text)`.
2. **تطبيع (lowercase)** + إزالة **stopwords** (كلمات مثل the/and/for لا تحمل معنى).
3. **Stemming** (تقليص اللاحقات): `charged→charg`, `refunds→refund`, `plans→plan`.

الكود:
```python
def _stem(word):
    if word.endswith("ing") and len(word)>=6: word = word[:-3]
    elif word.endswith("ed") and len(word)>=5: word = word[:-2]
    elif word.endswith("ies") and len(word)>=5: word = word[:-3]+"y"
    ...
```
**لماذا الـ Stemming؟** حتى تلتقي "charged" في السؤال مع "charge" في الوثيقة → تشابه أعلى.

### 1.5.2 وزن TF و IDF
لكل كلمة في مقطع:
```
TF  = (1 + log(عدد مرات ظهور الكلمة))          # تكرار داخل الوثيقة
IDF = log((1+N)/(1+عدد الوثائق التي تحويها)) + 1  # ندرة الكلمة عبر الكوربوس
weight = TF × IDF
```
- **TF:** كل تكرار يزيد الوزن (بالتدريج اللوغاريتمي).
- **IDF:** كلمة نادرة (مثل "crawler") وزنها أعلى من كلمة شائعة (مثل "the") — لأنه لو كلمة موجودة في كل الوثائق لا تميّز شيئًا.

الكود:
```python
weight = (1.0 + math.log(count)) * self._idf.get(term, 0.0)
```

### 1.5.3 التطبيع (Normalization)
بعد حساب أوزان كل الكلمات، نقسم على طول المتجه (Euclidean norm) لتكون بصمة كل مقطع **بطول 1**:
```python
norm = sqrt(Σ weight²)
vec = {term: w / norm for term, w in vec.items()}
```
**لماذا؟** حتى نمنع الوثائق الطويلة من الفوز بمجرد طولها. التطبيع يجعل المقارنة **عادلة** (تشابه زاوي).

### 1.6 البحث Cosine Similarity (`search`)
لحظة البحث:
1. نحوّل **نص السؤال** إلى متجه بنفس الطريقة (نفس الـ IDF).
2. نطبّعه.
3. نحسب **الضرب النقطي** بين متجه السؤال ومتجه كل مقطع:
```python
score = Σ (q_vec[term] * doc_vec[term])
```
لأن المتجهين مطبّعين (طول 1)، فالضرب النقطي = **cosine زاوية بينهما** (0 = لا تشابه، 1 = تطابق تام).
4. نرتّب تنازليًا ونأخذ أفضل `top_k`.

**تصفية المجال:** `if domain and chunk.domain != domain: continue` — نتجاهل أي مقطع من منتج آخر.
**العتبة:** نتجاهل أي نتيجة `score <= min_score` (افتراضيًا 0.10).

---
## 1.7 مثال رقمي كامل (كيف تُحسب النتيجة بالأرقام)

لنفترض كوربوس صغير من **3 مقاطع** (للفهم فقط):

```
D1 = "claude crawl block robots"     (الوثيقة الصحيحة)
D2 = "claude api rate limit"
D3 = "visa card payment"
```

والسؤال: **"How does Claude crawl..."** → tokens السؤال = `{claude, crawl}` (بعد إزالة stopwords).

### الخطوة 1 — حساب IDF لكل كلمة

| الكلمة | تظهر في كم وثيقة (df) | IDF = ln((1+N)/(1+df))+1 |
|---|---|---|
| `claude` | 2 (D1, D2) | ln(4/3)+1 = **1.2877** |
| `crawl` | 1 (D1) | ln(4/2)+1 = **1.6931** |

→ لاحظ أن `crawl` (نادرة) وزنها أعلى من `claude` (شائعة نسبيًا). هذا جوهر IDF.

### الخطوة 2 — متجه السؤال (مطبّع)

```
q_raw  = { claude: 1×1.2877 = 1.2877 , crawl: 1×1.6931 = 1.6931 }
norm   = sqrt(1.2877² + 1.6931²) = sqrt(1.658 + 2.867) = 2.127
q_unit = { claude: 1.2877/2.127 = 0.605 , crawl: 1.6931/2.127 = 0.796 }
```

### الخطوة 3 — متجه D1 (مطبّع)

كلمات D1 = {claude, crawl, block, robots}، وكل من block/robots يظهر مرة واحدة → IDF=1.6931.

```
d1_raw  = { claude:1.2877 , crawl:1.6931 , block:1.6931 , robots:1.6931 }
norm    = sqrt(1.2877² + 3×1.6931²) = sqrt(10.259) = 3.203
d1_unit = { claude:0.402 , crawl:0.529 , block:0.529 , robots:0.529 }
```

### الخطوة 4 — cosine(q, D1) = الضرب النقطي

```
= q_unit[claude]×d1_unit[claude] + q_unit[crawl]×d1_unit[crawl]
= 0.605×0.402 + 0.796×0.529
= 0.243 + 0.421
= 0.664   ← تشابه عالٍ
```

### الخطوة 5 — المقارنة مع D2

D2 لا تحتوي "crawl"، فقط "claude":
```
cosine(q, D2) = 0.605×0.402 = 0.243   ← أقل بكثير
```
والـ D3 لا تحتوي أي كلمة من السؤال → 0.

**النتيجة:** `D1 (0.664) > D2 (0.243) > D3 (0)` → الوكيل يسترجع **D1** الصحيحة. 

> 📌 هذا بالضبط ما يحدث في الكود: `search()` تُرجع أعلى 4 مقاطع بهذه الطريقة، والوكيل يستخدم الأول مادةً للردّ + `product_area`.

---

## 2) `rules.py` — العقل والقواعد

### 2.1 تحديد المجال (`infer_domain`)
```python
def infer_domain(company, text):
    comp = company.strip().lower()
    if comp in {"hackerrank","claude","visa"}:   # معلوم مباشرة
        return comp
    # وإلا استنتج من الكلمات عبر _DOMAIN_KEYWORDS
    best, best_score = None, 0
    for domain, kws in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text.lower())
        if score > best_score: best, best_score = domain, score
    return best if best_score > 0 else None
```
- `_COMPANY_TO_DOMAIN` يترجم "HackerRank"→`hackerrank`, "Claude"→`claude`, "Visa"→`visa`.
- إذا `company = None` → نستنتج من الكلمات. مثال: نص يقول "carte Visa bloquée" → كلمة "visa" → `visa`.
- إذا فشل الاستنتاج → `None` (نبحث في كل المجالات، ونقلّل الثقة).

### 2.2 تصنيف نوع الطلب (`classify_request_type`)
الترتيب مهم:
```python
# 1) خارج النطاق/خبيث/تحية فقط → invalid
if detect_out_of_scope(text):
    return "invalid"

# 2) feature request
if re.search(r"can we extend|can you add|it would be (nice|great)|please add \w+", text, re.I):
    return "feature_request"

# 3) bug (استخدام/انقطاع)
if re.search(r"not working|doesn'?t work|is down|broken|error|failed|fails|crash|stopped|bug|instead|issue|unable to|can'?t see|blocker|showing error", text, re.I):
    return "bug"

# 4) الافتراضي: product_issue
return "product_issue"
```
**لماذا هذا الترتيب؟** نبدأ بالحالات الحاسمة (خبيث) ثم الأكثر تحديدًا (feature) ثم العرضي (bug) ثم الافتراضي.

### 2.3 كشف خارج النطاق (`detect_out_of_scope` + `_is_pure_greeting`)
```python
_OUT_OF_SCOPE_PATTERNS = ["iron man", "who is", "what is the name of",
                          "write me a poem", "delete all files", "give me the code to",
                          "rm -rf", "format my", "are you a robot", "tell me a joke", ...]

def _is_pure_greeting(text):
    stripped = re.sub(r"[^a-z0-9 ,.!?']", " ", text.lower()).strip()
    if _PURE_GREETING_RE.match(text): return True            # "thanks"/"hello" فقط
    words = re.split(r"\s+", stripped)
    if len(words) <= 5 and _SHORT_GREETING_RE.search(text):  # جملة قصيرة جدًا فيها تحية
        return True
    return False
```
**نقطة دقيقة ومهمة:** في أول نسخة كانت "Hello"/"Thank you" تُصنّف خطأً `invalid` حتى لو كانت التذكرة حقيقية. الحل: نعتبر التحية "خارج النطاق" **فقط** إذا كانت الرسالة **كلها** تحية/شكر، لا مجرد بداية/نهاية مهذبة.
- تذكرة "Hello! I want to remove an interviewer" → **ليست** تحية نقية → تُعالج عادةً. ✅
- تذكرة "Thank you for helping me" → تحية نقية (قصيرة، بلا طلب) → `invalid`. ✅

### 2.4 فحص التصعيد (`decide_escalation`)
يمرّ على ثلاث مجموعات بالترتيب:
```python
# A) إجراءات مميزة لا ننفّذها
for pat, reason in _ACTION_ESCALATION_PATTERNS:
    if re.search(pat, text, re.I): return reason
# B) إشارات عالية الخطورة
for pat, reason in _RISK_PATTERNS:
    if re.search(pat, text, re.I): return reason
# C) انقطاع عام
for pat in _OUTAGE_PATTERNS:
    if re.search(pat, text, re.I): return "system-outage"
return None
```
أمثلة على الإجراءات (A):
```python
(r"increase my score",      "score/result modification")
(r"restore my access",      "account access restoration")
(r"refund me",              "billing/refund action")
(r"pause our subscription", "subscription/billing change")
(r"(name|certificate).*(incorrect|wrong)", "certificate/data modification")
```
أمثلة على المخاطر (B): `payment`, `billing`, `refund`, `chargeback`, `order id`, رقم بطاقة (regex `\d{13,19}`), `identity theft`, `fraud`, `security vulnerability`, `minor`, `suicid`, `medical`, `legal`, `gdp[ar]`.

### 2.5 مؤشرات الأسئلة (`is_how_to`)
يجمع قوالب مثل "how do", "what is", "report", "set up", "next steps" — تُستخدم للتفريق بين حالة "أستطيع الردّ لكن الاسترجاع ضعيف" (نطلب تفاصيل) وحالة "لا أعرف" (نصعّد).

---

## 3) `response.py` — بناء الرد (Grounding إجباري)

### 3.1 اختيار نص الإجابة (`_select_answer_text`)
```python
top = hits[0]
if len(top.chunk.text) >= 220:
    return _strip_markdown(top.chunk.text)     # استخدم أفضل مقطع مباشرة
# وإلا: ادمج مقاطع متتالية من نفس المقال حتى يكتمل المعنى
```
**الفكرة:** الرد = **نص المقالة حرفيًا**، بلا أي إضافة من معرفة الموديل. هذا يضمن "لا اختلاق".

### 3.2 الاقتطاع (`_trim_sentences`)
```python
clean = re.sub(r"[ \t]+", " ", text).strip()
if len(clean) <= 1080: return clean
cut = clean[:1080]
idx = cut.rfind(". ")        # اقتطع عند نهاية جملة
return clean[:idx+1]
```
نُبقي الرد مقروءًا (نقتطع عند نقطة، لا في منتصف كلمة).

### 3.3 رسائل التصعيد/الرفض/طلب التفاصيل
```python
def escalation_message(reason, domain):
    return f"...sensitive or high-risk area ({reason})... I've escalated it to a human..."

def out_of_scope_message():
    return "I'm sorry, this is outside the scope of what I'm able to help with..."

def need_more_info_message():
    return "Thanks for reaching out. I'd love to help, but I need a bit more detail..."
```
كلها رسائل ثابتة آمنة — **لا تدّعي معلومة غير موجودة في الكوربوس**.

---
## 4) `main.py` — شجرة القرار النهائية (`_handle_ticket`)

هذه أهم دالة. تدمج كل الإشارات وتقرّر. لنشرحها بالترتيب الذي تُنفّذ به فعليًا:

### الخطوة التمهيدية — جمع الإشارات
```python
text = f"{issue} {subject}".strip()
domain = infer_domain(company, text)
request_type = classify_request_type(text)
esc = decide_escalation(text)
oos = detect_out_of_scope(text)
malicious = is_malicious(text)
vague = detect_vague(text)
howto = is_how_to(text)
hits = index.search(text, top_k=4, domain=domain, min_score=MIN_SCORE)
grounded = bool(hits) and hits[0].score >= MIN_SCORE
product_area = hits[0].chunk.product_area if hits else _default_product_area(domain)
```

### الفروع الخمسة (بالترتيب)

**الفرع 1 — خبيث أو خارج النطاق:**
```python
if malicious or oos:
    return {status: "replied", request_type: "invalid",
            response: out_of_scope_message(), ...}
```
مثال: "give me the code to delete all files" → `invalid` + ردّ رفض.

**الفرع 2 — تصعيد صارم (hard escalate):**
```python
if esc and esc in _HARD_ESCALATIONS:
    return {status: "escalated", response: escalation_message(esc, domain), ...}
```
`_HARD_ESCALATIONS` = كل الأسباب التي **لا يمكن** حلّها تلقائيًا: تغيير درجة، استعادة وصول/مقعد، استرداد، حظر بائع، تعديل شهادة، إيقاف اشتراك، دفع/فاتورة/بطاقة، قاصر/إيذاء النفس/طبي/قانوني، انقطاع.
مثال: "pause our subscription" → `escalated`.

**الفرع 3 — تصعيد "ناعم" (soft) لكن قد يكون قابلًا للردّ:**
```python
if esc:   # esc موجود لكنه ليس في HARD (مثل fraud / identity / security)
    if grounded and howto:
        return {status: "replied", response: reply_message(...)}   # وثّق → ارد
    return {status: "escalated", ...}                               # لم يوثّق → صعّد
```
**الفكرة:** إذا كان الطلب عن "كيف أُبلّغ عن..." ووجدنا دليلًا واضحًا في الكوربوس → نردّ بالدليل. غير ذلك → نصعّد.
مثال: "I found a security vulnerability, what are the next steps" → وُجدت وثيقة bug bounty → `replied`.

**الفرع 4 — لا يوجد تصعيد، والاسترجاع موثّق:**
```python
if grounded:
    return {status: "replied", response: reply_message(...), ...}
```

**الفرع 5 — لا يوجد تصعيد، ولم يوثّق الاسترجاع:**
```python
if howto and not vague:
    return {status: "replied", response: need_more_info_message(), ...}  # اطلب تفاصيل
return {status: "escalated", response: escalation_message("no reliable grounding", ...)}
```
**الفكرة:** إذا كنا لا نعرف ولم نجد دليلًا → **لا نخمّن**. إما نطلب تفاصيل (سؤال how-to) أو نصعّد.

### تمثيل بصري للشجرة
```
                 [malicious or oos?] ──yes──► invalid + رفض
                        │no
              [esc in HARD_ESC?] ──yes──► escalated (إجراء/مخاطر/انقطاع)
                        │no
                 [esc موجود؟] ──yes──► [grounded & howto?] ──yes─► replied
                        │no                     └no────────────► escalated
             [grounded (وثيقة موثّقة)؟] ──yes──► replied (نسخ)
                        │no
                [howto & not vague?] ──yes──► replied (اطلب تفاصيل)
                        │no
                        └──────────────────────► escalated (لا دليل)
```

> 🔑 **القاعدة الذهبية:** الترتيب مقصود. الأمان أولًا، ثم الاسترجاع، ثم القرار. لا "توليد" بلا توثيق = لا هلوسة.

---

## 5) لماذا لم تستخدم LLM؟ ومتى تحتاجه؟

في هذا الحل اخترت **بلا LLM** لأسباب عملية:
- **Determinism:** نفس المدخل → نفس المخرج دائمًا (مطلوب للتقييم).
- **صفر مفاتيح/شبكة:** يعمل offline، ولا يفشل بسبب API.
- **صفر اختلاق:** الرد منسوخ حرفيًا.
- **10/10** على العيّنات المرجعية للـ status وrequest_type.

**متى تحتاج LLM؟** عندما تكون جودة الاسترجاع الدلالي ضعيفة (مثل: "remove an interviewer" استرجع "try a question"). الحل المثالي: **Hybrid** — قواعد حتمية للأمان + LLM/Embeddings لفهم المعنى. لكن ابقِ قرار التصعيد حتميًا (rules) لأن الأمان لا يُترك للموديل وحده.

---

## 6) مفاهيم يجب أن تعرفها للمقابلة

### TF-IDF vs Embeddings
| | TF-IDF (ما استخدمناه) | Embeddings |
|---|---|---|
| التمثيل | أوزان كلمات | متجهات معنى (768–1536 بُعد) |
| الفهم | حرفي (كلمة بكلمة) | دلالي (يفهم المعنى) |
| المثال | "car" ≠ "automobile" | "car" ≈ "automobile" |
| السرعة | فوري، بلا API | يحتاج نداء API + تخزين |
| الخصوصية | محلي 100% | يرسل النص لمزوّد |

### BM25
نسخة مطوّرة من TF-IDF تُعالج:
- تشبّع طول المستند (document length normalization) بشكل أفضل.
- تشبّع تكرار الكلمة (تأثير TF بتشبّع log/log).
عمليًا أعلى دقة من TF-IDF الخام في معظم بيانات الدعم.

### RAG (Retrieval-Augmented Generation)
النمط الذي استخدمناه: **استرجع أولًا، ثم أنشئ/اختر الإجابة بناءً على المسترجَع فقط.** هذا يقلّل الهلوسة ويربط الرد بمصدر.

### Agents
وكيل = LLM + أدوات (tools) + حلقة قرار (loop). الفرق عن مجرد prompt: الوكيل **يقرّر** أي أداة يستخدم ومتى (بحث، تصنيف، حساب...). في 24 ساعة، ابدأ بخط أنابيب ثابت (كما هنا)، وأضف أدوات إن اتّسع الوقت.

### كيف تجيب في المقابلة عن "أين يفشل حلك؟"
> "يفشل في الحالات التي تتطلب فهمًا دلاليًا: مثلاً حين تختلف الكلمات بين السؤال والوثيقة (synonyms). أيضاً التذاكر شديدة الغموض التي لا تحتوي تفاصيل كافية. الحل المقترح: Hybrid Retrieval (BM25 + Embeddings) مع إبقاء قواعد الأمان حتمية."

---

## 7) خلاصة تنفيذية (Takeaways)

1. **ابنِ الفهرس حسب العناوين** — أهم قرار في الاسترجاع.
2. **رجّح العنوان** (×2) — عناوين مراكز المساعدة = صياغة السؤال.
3. **عتبة الثقة → تصعيد** عند الشك — لا ردود ضعيفة.
4. **افصل الأمان عن التوليد** — rules حتمية للإجراءات/المخاطر، ونسخ موثّق للردود.
5. **رتب الفحوص** — خبيث → إجراء مميز → خطر ناعم → موثّق → طلب تفاصيل/تصعيد.
6. **قِس على العيّنات المرجعية** (`sample_support_tickets.csv`) وكرّر الضبط.
7. **وثّق وثبّت القرارات** (determinism) — ترفع درجة التصميم والمقابلة.
---

## 8) هل يوجد Agent Loop؟ وكيف يقرّر عند الفشل؟ (Does it loop? How does it decide on failure?)

سؤال مهم جدًا للمقابلة. الإجابة المختصرة: **لا يوجد loop في النسخة الحالية، ونعم يقرّر عند الفشل (يُصعّد بأمان).**

### 8.1 الفرق بين Pipeline و Agent Loop

| | Pipeline (حالتنا الحالية) | Agent Loop (ReAct) |
|---|---|---|
| المسار | **مسطّح لمرة واحدة**: classify → route → retrieve → respond | **حلقة**: Reason → Act(tool) → Observe → كرّر |
| عدد المرات | مرة واحدة لكل تذكرة | عدة تكرارات حتى يقرّر التوقف |
| متى تحتاجه | عندما تكون الخطوات**معروفة ومحدودة** | عندما يحتاج الوكيل أن **يجرّب ويتحقّق** (بحث متعدد، أدوات) |
| المخاطرة | منخفضة (حتمي) | عالية (قد يدخل loop لا نهائي/يزيد التكلفة) |

**حالتنا**: خط أنابيب ثابت. لماذا لا loop؟ لأن كل تذكرة تحتاج نفس الخطوات بالضبط بلا قرار وسيط — لا حاجة لإعادة محاولة أو بحث متعدد. هذا **أسرع وأرخص وأكثر حتمية**.

**متى تضيف loop؟** فقط إذا أضفت LLM + أدوات (tools) فيه عدم يقين، مثل: "هل أجرب بحثًا بصيغة أخرى؟ هل أحتاج أداة ثانية؟". حينها:

```python
MAX_STEPS = 5
for step in range(MAX_STEPS):
    thought = llm.reason(state)
    action  = thought.action          # search_corpus / classify / escalate / finalize
    if action == "finalize":
        break                          # شرط التوقف
    observation = run_tool(action)
    state.append(observation)          # Observe
else:
    decision = "escalate"              # بعد استنفاد المحاولات → تصعيد (أمان)
```

**قواعد سلامة الـ loop (لو أضفتها):**
1. **سقف تكرارات (MAX_STEPS)** — يمنع الـ infinite loop.
2. **شرط توقّف واضح** (`finalize` أو `escalate`).
3. **كشف عدم التقدّم** — لو لم تتغيّر الحالة بين خطوتين، توقّف وصعّد.
4. **مهلة زمنية** لكل خطوة (timeout).
5. **fail-safe في النهاية** — إن لم يُحسم → تصعيد.

### 8.2 كيف يقرّر الوكيل عند الفشل (Fail-safe)

المبدأ: **الفشل لا يوقف النظام ولا يخمّن — بل يُصعّد لإنسان.** الكود يعالج أربعة أنواع من الفشل:

**1) فشل "ناعم" — استرجاع ضعيف:** لا توجد وثيقة موثوقة (score < العتبة):
```python
grounded = bool(hits) and hits[0].score >= MIN_SCORE
...
if grounded:  # ردّ موثّق
    ...
else:         # لا دليل → تصعيد أو طلب تفاصيل (لا تخمين)
    return {status: "escalated", ...}
```

**2) استثناء في الاسترجاع (خطأ برمجي/بيانات):**
```python
try:
    hits = index.search(text, top_k=TOP_K, domain=domain, min_score=MIN_SCORE)
except Exception as exc:
    logging.error("Retrieval failed for ticket %r: %s", text[:80], exc)
    hits = []          # عامله كأنه لا يوجد دليل → يقع في فرع التصعيد
```

**3) استثناء لتذكرة واحدة — لا يجب أن يُسقِط الدفعة كلها:**
```python
try:
    decision = _handle_ticket(issue, subject, company, index)
except Exception as exc:
    logging.error("Ticket %d failed: %s", i, exc)
    decision = {                      # صف تصعيد آمن بدل الانهيار
        "status": "escalated",
        "request_type": "product_issue",
        "response": escalation_message("processing error", "unknown"),
        "justification": "Processing error; escalated to a human (fail-safe).",
    }
```
بهذا **تُنتَج دائمًا نفس عدد الصفوف** حتى لو فشلت تذكرة — مهم جدًا للتسليم.

**4) فشل على مستوى التشغيل (ملف مفقود / كوربوس فارغ):**
```python
if not chunks:
    print("[error] No corpus documents found.", file=sys.stderr); return 1
if not tickets.exists():
    print(f"[error] Tickets file not found: {tickets}"); return 1
```
خطأ واضح + كود خروج ≠ 0 (لا انهيار صامت).

**5) داخل الكوربوس — ملف تالف:** `load_corpus` يلفّ القراءة في `try/except` ويسمح بتجاهل الملف دون تعطيل الباقي. (Fail-soft على مستوى الملف الواحد.)

### 8.3 مصفوفة القرار عند الفشل

| نوع الفشل | القرار | المخرج |
|---|---|---|
| استرجاع ضعيف (< العتبة) | Escalate | `escalated` |
| استثناء في البحث | Escalate | `escalated` |
| استثناء في تذكرة | Escalate | `escalated` (صف آمن) |
| ملف تذكرة مفقود | توقّف واضح | exit code 1 |
| كوربوس فارغ | توقّف واضح | exit code 1 |
| ملف كوربوس تالف | تجاهل | يُكمل |
| (LLM) فشل/JSON غير صالح | Retry → fallback → escalate | `escalated` |

> 🔑 **القاعدة:** في وكيل دعم، **الفشل الآمن = التصعيد**، لا الانهيار ولا التخمين. وهذا بالضبط ما يسمّى "decide on failure".

### 8.4 إجابة نموذجية للمقابلة
> "لا يحتاج هذا التصميم حلقة (loop) لأن الخطوات محدّدة ومتتابعة، وهذا يعطينا حتمية وسرعة. لكن التصميم **fail-safe**: أي فشل — استرجاع ضعيف، استثناء، أو حتى تذكرة فاسدة — لا يُسقط النظام؛ بل يُعالج ويُصعّد لإنسان بدل التخمين. ولو رقّينا لوكيل LLM بأدوات، سنضيف loop مع سقف تكرارات (MAX_STEPS) وشرط توقّف ومهلة زمنية، مع بقاء قرار التصعيد حتميًا."
