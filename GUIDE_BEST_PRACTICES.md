# دليل بناء وكيل دعم فني احترافي خلال 24 ساعة

> دليل عملي مفصّل لكيفية بناء مشروع مثل تحدي **HackerRank Orchestrate — Support Agent** خلال Hackathon مدّته 24 ساعة، مع أفضل الممارسات في الـ Agent Architecture، وRAG، واختيار الموديل، وسلامة الكود، وError Handling.

---

## 1) نظرة عامة على التحدي (What you're actually building)

التحدي: **وكيل دعم فني** يقرأ تذاكر دعم من 3 منتجات (HackerRank / Claude / Visa)، ويردّ عليها **فقط بالاستناد إلى كوربوس دعم محلي** مرفق مع المشروع.

**المطلوب لكل تذكرة:**

| العمود | القيم |
|---|---|
| `status` | `replied` / `escalated` |
| `request_type` | `product_issue` / `feature_request` / `bug` / `invalid` |
| `product_area` | الفئة الأكثر صلة |
| `response` | إجابة موثّقة |
| `justification` | شرح القرار |

**المدخلات:** `issue` + `subject` (قد تكون فارغة/مشوشة/مضللة) + `company` (قد تكون `None`).

**الشروط الصارمة:**
- يعمل في **الطرفية** (terminal-based).
- **يستخدم فقط الكوربوس المرفق** — ممنوع استخدام المعرفة الخارجية أو البحث الحي.
- **ممنوع الاختلاق** (no hallucinated policies / unsupported claims).
- **التصعيد** (escalate) للحالات الحساسة/عالية الخطورة/غير المدعومة بدل التخمين.

---

## 2) خطة الـ 24 ساعة (Optimal 24-hour plan)

أهم درس في الهاكاثون: **لا تبدئي بالكود، ابدئي بالفهم.** توزيع الوقت المثالي:

| المدة | المرحلة | ما تفعله |
|---|---|---|
| **0–1.5 س** | فهم المشكلة | اقرأ `problem_statement.md` + `evalutation_criteria.md` + `README.md`. افهم **إزاي بينقّط الـ grader**: code design + output accuracy + judge interview + AI fluency. |
| **1.5–3 س** | استكشاف الكوربوس | اقرأ ملفات من كل منتج، افهم صيغة الـ markdown/front-matter، حدّد الفئات (categories) وسُمّي الـ product areas. |
| **3–6 س** | بناء الأساس (MVP) | أنشئ `loader + chunker + retriever + classifier + router + responder`. **ابدئي بخط أنابيب يعمل end-to-end حتى لو بدائي.** |
| **6–11 س** | تحسين الجودة | حسّن الاسترجاع (weighting/BM25/hybrid)، أضف قواعد الأمان والتوجيه، حسّن الـ responses. |
| **11–13 س** | التقييم والضبط | شغّل على `sample_support_tickets.csv` (عندهاج إجابات مرجعية) وقارن. كرّر الضبط حتى تحصل على دقة عالية. |
| **13–16 س** | Run على البيانات الحقيقية | أنتج `output.csv` على `support_tickets.csv`، راجعه سطرًا سطرًا، وعدّل القواعد. |
| **16–20 س** | التوثيق والتصميم | `code/README.md`، كود نظيف وموديولار، تعليقات، تحديد القرارات (determinism). |
| **20–24 س** | التسليم + تحضير المقابلة | ارفع `code/`، `output.csv`، `log.txt`. جهّز شرحًا لـ "ليه اخترت كذا؟" و"فين بتنهار؟". |

**القاعدة الذهبية خلال 24 ساعة:** اجعل شيئًا يعمل **end-to-end** مبكرًا، ثم حسّنه. لا تقضي وقتًا طويلًا في البحث عن "الحل الأمثل" قبل أن يكون لديك خط أنابيب يعمل.

---

## 3) فهم معايير التقييم (How you're scored)

قراءة `evalutation_criteria.md` تحدد قراراتك كلها:

1. **Agent Design (الكود):** يقيمون وضوح الفصل بين المسؤوليات (retrieval/reasoning/routing/output)، وبرر اختيار التقنية، واستخدام الكوربوس فعليًا (grounding)، ووجود تصعيد صريح، وdeterminism، ونظافة الكود.
2. **AI Judge Interview (30 دقيقة):** يسألون: لماذا اخترت هذا التصميم؟ ما البدائل التي رفضتها؟ أين يفشل الوكيل؟ كن صادقًا بشأن ما صممته أنت مقابل ما ولّده الـ AI.
3. **Output CSV:** لكل صف يقيمون الأعمدة الخمسة: صحة `status`، وصحة `product_area`، و`response` (موثّق/مفيد/غير مختلق)، و`justification`، وصحة `request_type`.
4. **AI Fluency (log.txt):** يقرؤون سجلّ المحادثة ليروا إن كنت تقود الـ AI وتتحقق منه، لا أن تتقبّله عمياء.

**الخلاصة:** أعلى نقاط تأتي من **كود نظيف + output صحيح + شرح واثق**. لذلك استثمر في الثلاثة.

---

## 4) بنية الوكيل (Agent Architecture)

### Single-Agent vs Multi-Agent

| | Single-Agent (Pipeline) | Multi-Agent (Roles) |
|---|---|---|
| **السرعة** | ⚡ أسرع في البناء والتشغيل | أبطأ وأكثر تكلفة |
| **الوضوح** | سهل الشرح والتعقب | أكثر "ذكاءً" لكن أسهل للارتباك |
| **الصلاحية للـ 24 ساعة** | ✅ **الأفضل** | ❌ قد يستهلك الوقت |

**في 24 ساعة، أوصي بـ Single-Agent Pipeline** بمسؤوليات واضحة. يمكنك لاحقًا إظهار "تعدد الوكلاء" كمفهوم في المقابلة دون تعقيد التنفيذ.

### أركان الخط أنابيب (Pipeline)

```
Load Corpus -> Chunk (by heading) -> Embed/Index -> Retrieve (top-k)
   -> Classify (request_type) -> Route (reply/escalate) -> Ground/Generate response
   -> Justify -> Handle safety/edge cases
```

### مسؤوليات كل مرحلة

- **Loader/Chunker:** يحوّل الكوربوس إلى وحدات قابلة للاسترجاع. الـ markdown headings هي الحدود الطبيعية.
- **Retriever:** يجيب "ما الوثيقة الأقرب لهذه التذكرة؟".
- **Classifier:** يحدد `request_type`.
- **Router (Safety):** يقرر `replied` vs `escalated` بناءً على المخاطر.
- **Responder:** ينتج `response` موثّقة (grounded) + `justification`.
- **Guardrails:** كشف الـ PII/الأرقام، والبرومبت-إنجكشن، والحالات الخارجة عن النطاق، والغموض.

### لماذا هذا التقسيم أفضل؟

- **قابل للقراءة والشرح** (المقابلة ستطلب ذلك).
- **قابل للتعديل** (تقدر تغيّر الـ retriever أو الـ classifier دون كسر الباقي).
- **قابل للتتبع** — تستطيع تسجيل كل قرار (log) لتبريره لاحقًا.

---
## 5) تقنيات الاسترجاع (Retrieval / RAG)

هذه أهم مرحلة في الحل — لأن جودة الـ `response` و`product_area` تعتمد كليًا على جودة الاسترجاع.

### خيارات الاسترجاع (من الأبسط للأقوى)

| التقنية | الوصف | متى تستخدمها؟ |
|---|---|---|
| **Keyword / TF-IDF** | عدّ تكرار الكلمات + IDF. سريع، بلا مكتبات. | MVP، أو عندما لا تملك كيانات/model. |
| **BM25** | تطوير TF-IDF يُحسّن الترتيب للنصوص الطويلة والاستعلامات القصيرة. أفضل بكثير من TF-IDF الخام. | ✅ خيار ممتاز للـ 24 ساعة. |
| **Embeddings (Vector DB)** | تحويل النص إلى متجهات (vectors) عبر نموذج تضمين مثل `text-embedding-3-small`، ثم بحث بالتشابه. | ✅ الأفضل للجودة، يتطلب API. |
| **Hybrid (BM25 + Vectors)** | دمج النتائج من الاثنين (weighted/reciprocal rank fusion). | 🏆 الأفضل عمليًا. |

### خطوات تنفيذ RAG قوية

1. **Chunking ذكي:** قسّم حسب **العناوين (headings)**، واحتفظ بـ `title + breadcrumbs + source`. لا تقسّم عشوائيًا بحجم ثابت — الـ headings هي حدود المعنى.
2. **احتفظ بالميتاداتا:** `title`, `breadcrumbs`, `article_id`, `domain`, `category` — تستخدمها لاحقًا لـ `product_area` و`justification`.
3. **قوّي الـ query:** ادمج `issue + subject`، وأضف `company` كـ filter (وإن كان `None` قلّل الثقة).
4. **Weighting:** أعطِ وزنًا أعلى لعنوان/عنوان فرعي المقالة، لأن عناوين مراكز المساعدة تعيد صياغة السؤال حرفيًا.
5. **Threshold:** إذا كانت أفضل نتيجة أقل من عتبة (threshold) — **صعّد** (escalate) بدل أن تردّ بإجابة ضعيفة. قاعدة: "لا أردّ إلا إذا كنت متأكدًا؛ وإلا فالإنسان".
6. **Domain filter:** حدّد البحث داخل مجال `company` لرفع الدقة ومنع تداخل المنتجات.

### مثال عملي (من حلي الحالي)

```python
# 1) قسّم المقالات حسب الـ headings
for idx, (heading, text) in enumerate(_chunk_body(body)):
    tokens = tokenize(f"{title} {title} {heading} {text}")  # وزّن العنوان مرتين

# 2) ابنِ فهرس TF-IDF مع وزن أعلى للعنوان
# 3) ابحث ضمن المجال فقط:
hits = index.search(query, top_k=4, domain=domain, min_score=MIN_SCORE)
# 4) إذا لم تصل العتبة => escalate
```

### لماذا تهتم بالاسترجاع؟

لأنّ الـ grader يقيّم `response` بأنها "موثّقة، مفيدة، غير مختلقة". إذا استرجعنا وثيقة **غير صحيحة**، حتى لو كانت حرفية من الكوربوس، فهي **غير مفيدة**. لذلك الاسترجاع الدقيق أهم من صياغة الرد نفسها.

---

## 6) اختيار الموديل (Best Model & Techniques)

### أفضل الموديلات للـ 24 ساعة

| الموديل | متى تستخدمه | لماذا |
|---|---|---|
| **Claude 4 / Claude Sonnet** | الردّ على التذاكر وتصنيفها وكتابة `justification` | ممتاز في الفهم واللغة الطبيعية والـ tool use |
| **GPT-4o / GPT-4o-mini** | بديل رائع، سرعة ومرونة، يدعم Structured Outputs / JSON mode | طريقة صارمة لضمان شكل الإخراج |
| **Gemini 1.5/2.0 Pro** | إذا كان لديك أطول context | يقرأ كُتُبًا كاملة بمرور واحد |

**نصيحة:** إن لم تكن النتائج تعتمد على فهم عميق، يمكنك حتى بناء حلّ **بدون LLM** (كما فعلتُ في حليّ الحالي) لضمان الـ determinism وعدم الاختلاق، لكن مقابل جودة استرجاع دلالي أقل.

### تقنيات مهمة مع أي LLM

1. **Structured Output / JSON mode:**
   ```json
   {
     "status": "replied | escalated",
     "request_type": "product_issue | feature_request | bug | invalid",
     "product_area": "...",
     "response": "...",
     "justification": "..."
   }
   ```
   اجعل الموديل يُخرج JSON **متحققًا منه** (validate + retry) بدل نص حر.

2. **Tool Use / Function Calling:** أعطِ الوكيل أدوات مثل `search_corpus(query)`, `classify(text)`, `detect_risk(text)`. هذا يجعله "وكيلًا" حقيقيًا ويحسّن التتبع.

3. **Prompt Engineering:**
   - **System prompt** يحدد الدور والقواعد الصارمة: "استخدم فقط المعلومات من الكوربوس المرفق. إذا لم توجد معلومات كافية، صعّد."
   - **Few-shot examples** من `sample_support_tickets.csv` لتعليم الموديل النمط المتوقع.
   - **Grounding:** مرّر `Retrieved context` للموديل، واطلب الردّ عليه فقط.

4. **Chain-of-Thought (خفيف):** اطلب من الموديل أن يشرح المنطق قبل الإخراج (في `justification`) — يحسّن الجودة، لكن احذر من كشف منطق داخلي حساس في الـ responses للمستخدم.

### متى تستخدم LLM وأين؟

| المرحلة | LLM؟ | السبب |
|---|---|---|
| الاسترجاع (إعادة صياغة الـ query / التضمين) | ✅ | يحسّن ربط التذكرة بالوثيقة |
| تصنيف `request_type` | ✅ | يفهم النية بدقة |
| **قرار الأمان/التصعيد** | ⚠️ | يُفضّل **قواعد حتمية** (`detect PII/card/fraud`) + LLM كدعم — لا تترك الأمان للموديل وحده |
| توليد `response` | ✅ | صياغة طبيعية، لكن **مع grounding إجباري** |
| `justification` | ✅ | يشرح السبب بلغة طبيعية |

---

## 7) بنية الكود والـ Syntax (Clean Code)

### بنية مجلدات مثالية

```
code/
├── main.py            # CLI + orchestration (قراءة/كتابة CSV)
├── corpus_engine.py   # load + chunk + index + search
├── rules.py           # classification + routing + safety
├── response.py        # reply/escalate/out-of-scope builders
├── config.py          # tunables (thresholds, paths)
├── README.md          # كيفية التشغيل
└── tests/             # اختبارات (اختياري لكن مُحسِّن)
```

### قواعد الـ Syntax

- **فصل المسؤوليات:** ملف لكل مكوّن. لا تضع الوكيل كله في ملف واحد.
- **Type hints** في كل مكان — يُظهر النضج ويحمي من الأخطاء.
- **Dataclasses** للكيانات (`Chunk`, `Hit`, `TriageResult`) — أوضح من dicts.
- **Constants للـ magic numbers:** `MIN_SCORE = 0.10`, `TOP_K = 4` في `config.py`.
- **Determinism:** ثبّت البذور (`random.seed(42)`)، لا تعتمد على التوقيت أو الترتيب العشوائي.
- **Docstrings** قصيرة لكل module/function.
- **لا تكرار:** استخرج helper functions (`detect_sensitive`, `_trim`...).
- **أسماء معبّرة:** `is_sensitive`, `classify_request`, `build_reply` — لا `helper1`, `do_stuff`.

### مثال: فصل الأمان عن التوليد

```python
# rules.py — قرار
def decide_escalation(text: str) -> str | None:
    ...
    return reason  # أو None

# response.py — بناء الرد
def build_reply(question, hits, product_area, request_type) -> str:
    ...
```

هذا الفصل يسمح لك بتغيير `build_reply` دون لمس `decide_escalation`.

---
## 8) Error Handling & Robustness

في بيئة Hackathon، الأخطاء ليست "محتملة" — بل **مضمونة**. لذلك جهّز الخط أنابيب ليتحمّلها بأمان.

### مصادر الأخطاء الشائعة

| الخطأ | كيف تتعامل معه |
|---|---|
| ملف CSV **فارغ** أو بلا header | تحقق من `reader.fieldnames`، وأرجع رسالة خطأ واضحة. |
| عمود `issue` غير موجود | اكتشف الأعمدة بالاسم (case-insensitive)، وfallback على أسماء بديلة (`description`, `body`...). |
| `company = None` أو نص ضبابي | استنتج المجال من الكلمات، وإن عجزت **قلّل الثقة** أو صعّد. |
| نص **مشوش/مضلل/خبيث** | كاشف قواعد للـ PII/الأرقام/البرومبت-إنجكشن، ثم **refusal** (invalid) أو **تصعيد**. |
| استرجاع ضعيف (أفضل نتيجة < عتبة) | **صعّد** بدل الردّ على معلومة غير موثوقة. |
| LLM فشل/مهلة/خرج JSON غير صالح | **Retry** مع backoff، ثم fallback إلى الرد الموثّق من الاسترجاع (deterministic)، ثم التصعيد كملاذ أخير. |
| خطأ في ملف من الكوربوس | `try/except` حول القراءة، وتجاهل الملف بدل تعطيل العملية كلها. |
| تذكرة طويلة جدًا/فارغة | اقتطع (`_trim`)، واعالج الفارغ برسالة "هناك حاجة لمزيد من التفاصيل". |

### قواعد الـ Error Handling في الكود

1. **لا تبتلع الأخطاء بصمت.** `try/except` يجب أن يسجّل السبب ويقدّم fallback، لا أن يمرّ مرور الكرام.
2. **Fail-safe، لا Fail-fast.** في وكيل دعم، الفشل الآمن = **التصعيد**، لا إيقاف البرنامج أو ردّ خاطئ. خطأ محتمل → أرسله لإنسان.
3. **Validated струкtured output:** إذا كنت تستخدم LLM، تحقق من كل حقل (`status` في المجموعة المسموحة، إلخ)، وأعد المحاولة حتى 2–3 مرات، ثم fallback.
4. **Sentry-like logging:** سجّل كل تذكرة وقرارها ومصدرها (`log.txt`) — هذا أيضًا جزء من معيار **AI Fluency** ويساعدك في التتبع.
5. **حدود زمنية (timeouts)**: لكل استدعاء LLM مهلة، حتى لا تعلّق العملية على الشبكة.

### مثال: fail-safe على الاسترجاع

```python
try:
    hits = index.search(query, top_k=TOP_K, domain=domain, min_score=MIN_SCORE)
except Exception as exc:          # لا تبتلع الصمت — سجّل
    logging.error("Retrieval failed: %s", exc)
    hits = []                     # تعامل معه كأنه لا يوجد نتيجة موثوقة

if not hits or hits[0].score < MIN_SCORE:
    return escalated_response("no reliable grounding")   # صعّد بأمان
```

---

## 9) التقييم والتحقق (Evaluation)

بدون تقييم، لا تعرف إن كان حلك جيدًا. **عيّنتك الذهبية هي `sample_support_tickets.csv`** لأنها تحتوي الإجابات المرجعية.

### كيف تقيّم

1. **شغّل على `sample_support_tickets.csv`** وقارن `status` و`request_type` (والأفضل `product_area`) مع العمود المرجعي.
2. **احسب الدقة**:
   ```
   status_accuracy  = عدد التطابقات في status  / عدد الصفوف
   request_accuracy = عدد التطابقات في request_type / عدد الصفوف
   ```
   استهدف **عالية جدًا** (مثل 10/10).
3. **راجع `response` يدويًا** — هل هي موثّقة؟ مفيدة؟ غير مختلقة؟ جودة النص مهمة مثل التصنيف.
4. **اختبر حقيقتين أساسيتين:**
   - **لا اختلاق:** لا عبارة إجابة غير موجودة في الكوربوس.
   - **تصعيد صحيح:** جميع الحالات عالية الخطورة (دفع/هوية/أمن/انقطاع) تذهب إلى `escalated`.

### بناء سكربت تقييم بسيط

```python
import csv
for row in sample_rows:
    decision = agent.handle(row["Issue"], row["Subject"], row["Company"])
    assert decision.status.lower() == row["Status"].lower()
    assert decision.request_type.lower() == row["Request Type"].lower()
```

**كرّر الضبط:** غيّر عتبة الاسترجاع أو قواعد التوجيه، وأعد التقييم حتى تصل إلى النتيجة المطلوبة.

---

## 10) أفضل الممارسات النهائية + التسليم (Final Checklist)

### قائمة تحقق نهائية

- [ ] الكود يعمل `python main.py` بلا أخطاء.
- [ ] `code/README.md` يشرح كيفية التشغيل والمتطلبات.
- [ ] `output.csv` بالمخطط الصحيح (`status`, `request_type`, `product_area`, `response`, `justification`).
- [ ] لا **مفاتيح/أسرار** في الكود (استخدم `.env` + `os.getenv`، و`.gitignore`).
- [ ] **Deterministic** (بذور ثابتة، ترتيب ثابت).
- [ ] **تصعيد صريح** للحساسة/عالية الخطورة/غير المدعومة.
- [ ] **الأخطاء تُسجَّل** وتُعالَج بسلام (fail-safe → escalate).
- [ ] تقييم على `sample` بدقة عالية.
- [ ] الـ `response` موثّقة من الكوربوس (بدون اختلاق).

### للتسليم

1. **Zip مجلد `code/`** (استثنِ `data/`, `support_tickets/`, `.venv`, `node_modules`).
2. ارفع **`support_tickets/output.csv`** (نتائجك على البيانات الحقيقية).
3. ارفع **`log.txt`** (سجلّ المحادثة/التتبع).

### للمقابلة (30 دقيقة)

جهّز إجابات لـ:
- **لماذا اخترت هذا التصميم؟** (Single-agent pipeline، RAG خفيف، فصل الأمان عن التوليد).
- **ما البدائل التي رفضتها؟** (Multi-agent — أبطأ وأصعب تتبعًا؛ LLM خام بلا grounding — خطر الاختلاق؛ Keyword فقط — استرجاع دلالي ضعيف).
- **أين يفشل وكيلك؟** (الاسترجاع الدلالي؛ التذاكر شديدة الغموض؛ الصياغة عبر LLM قد تخرج عن النطاق — لذلك أبقيت الـ grounding إجباريًا).
- **ما دورك أنت مقابل الـ AI؟** (أنت قاد التصميم والقرارات؛ الـ AI نفّذ وتحققت منه).

---

## الخلاصة السريعة (TL;DR)

1. **افهم التحدي ومعايير التقييم أولًا** — هي تحكم كل قرار.
2. **ابنِ خط أنابيب يعمل end-to-end مبكرًا**، ثم حسّنه.
3. **استخدم RAG مع تشبّع حسب العناوين** + BM25/Embeddings للجودة، و**عتبة ثقة** → صعّد عند الشك.
4. **افصل الأمان عن التوليد** — قواعد حتمية للتصعيد + LLM للصياغة الموثّقة.
5. **استخدم LLM مثل Claude/GPT-4o** مع **Structured Output** و**Grounding إجباري** و**Few-shot** من الـ sample.
6. **عزّز الـ Error Handling** — لا تبتلع الأخطاء، والفشل الآمن = **التصعيد**.
7. **قيّم على `sample_support_tickets.csv`** وعدّل حتى دقة عالية.
8. **توثيق واضح + كود موديولار + determinism** — هي ما ترفع درجة تصميمك.
9. **جهّز شرحًا للمقابلة** — "ليه؟"، "فين يفشل؟"، "أنت فعلت إيه والـ AI فعل إيه؟".

> 💡 **حلّي المطبَّق:** وكيل حتمي بلا LLM (TF-IDF + قواعد) — ممتاز للـ determinism وعدم الاختلاق و10/10 على العيّنات، لكن لرفع جودة الاسترجاع الدلالي يمكنك ترقيته إلى BM25 أو Embeddings مع LLM للصياغة الموثّقة.
