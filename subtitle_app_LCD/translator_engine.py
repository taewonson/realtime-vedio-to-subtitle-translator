import deepl
from concurrent.futures import ThreadPoolExecutor

def translate_subtitles(segments, status_callback=None):
    def update_status(msg, percent):
        print(msg)
        if status_callback:
            status_callback(msg, percent)
            
    original_texts = [segment.text.strip() for segment in segments]
    update_status(f"[1/2] DeepL 다국어 일괄 번역 진행 중... (총 {len(original_texts)}문장)", 60)
    
    # 💡 여기에 DeepL 홈페이지에서 발급받은 API 키를 넣으세요!
    DEEPL_API_KEY = "420e266f-abb7-4686-8ea1-00e4c948e8ed:fx" 
    translator = deepl.Translator(DEEPL_API_KEY)
    
    # DeepL API가 요구하는 공식 타겟 언어 코드 (대문자)
    target_langs = ['KO', 'EN-US', 'JA', 'ZH', 'DE']
    translated_data = {'original': original_texts}
    
    def translate_language(lang):
        try:
            # 리스트를 통째로 던지면 DeepL이 알아서 초고속 일괄 번역합니다!
            results = translator.translate_text(original_texts, target_lang=lang)
            # 결과 객체에서 텍스트만 쏙 빼서 리스트로 반환
            return lang, [res.text for res in results]
        except Exception as e:
            print(f"[{lang}] DeepL 번역 오류: {e}")
            return lang, original_texts 

    # 5개 언어를 동시에 요이땅!
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(translate_language, target_langs)
        
        for lang, translated_texts in results:
            # UI 콤보박스에 맞게 언어 코드를 소문자로 다시 변환 ('EN-US' -> 'en')
            key_lang = lang.lower().replace('-us', '')
            translated_data[key_lang] = translated_texts
            update_status(f"  - DeepL {key_lang} 번역 완료!", 60 + target_langs.index(lang) * 7)

    update_status("[2/2] 최종 데이터 조립 중...", 95)
    
    subtitles = []
    for i, segment in enumerate(segments):
        texts_dict = {}
        for lang_key in ['original', 'ko', 'en', 'ja', 'zh', 'de']:
            try:
                texts_dict[lang_key] = translated_data[lang_key][i]
            except IndexError:
                texts_dict[lang_key] = original_texts[i]
                
        subtitles.append({
            'start': segment.start,
            'end': segment.end,
            'texts': texts_dict
        })

    update_status("✅ 모든 번역 완료!\n", 100)
    return subtitles