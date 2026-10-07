def generate_arabic_queries(question):
    prompt = f"""
أنت متخصص في البحث في كتب الفقه التراثية.

مهمة:
حلل سؤال المستخدم التالي، ثم أنشئ قائمة من عبارات البحث العربية
القصيرة والدقيقة التي يمكن استخدامها للبحث في كتب الفقه.

سؤال المستخدم:
{question}

الشروط:
- أنشئ من 3 إلى 8 عبارات بحث.
- استخدم مصطلحات فقهية عربية مناسبة.
- لا تجب عن السؤال.
- لا تشرح.
- لا تضف أي نص آخر.
- أخرج JSON فقط بالشكل التالي:

{{
  "queries": [
    "عبارة البحث الأولى",
    "عبارة البحث الثانية",
    "عبارة البحث الثالثة"
  ]
}}
"""

    try:
        response = gemini_generate(
            prompt,
            model=ARABIC_QUERY_MODEL
        )

        if not response:
            return []

        data = extract_json_object(response)

        if isinstance(data, dict):
            queries = data.get("queries", [])

            if isinstance(queries, list):
                cleaned = []

                for query in queries:
                    if not isinstance(query, str):
                        continue

                    query = query.strip()

                    if query and query not in cleaned:
                        cleaned.append(query)

                return cleaned[:8]

    except Exception as error:
        print(
            "❌ Arabic query generation error:",
            repr(error)
        )

    return []
