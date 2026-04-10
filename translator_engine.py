from deep_translator import GoogleTranslator
from concurrent.futures import ThreadPoolExecutor

def translate_subtitles(segments, status_callback=None):
    def update_status(msg, percent):
        print(msg)
        if status_callback:
            status_callback(msg, percent)
            
    original_texts = [segment.text.strip() for segment in segments]
    update_status(f"[1/2] 다국어 일괄 번역 진행 중... (총 {len(original_texts)}문장)", 60)
    
    target_langs = ['ko', 'en', 'ja']
    translated_data = {'original': original_texts}
    
    def translate_language(lang):
        try:
            translator = GoogleTranslator(source='auto', target=lang)
            return lang, translator.translate_batch(original_texts)
        except Exception as e:
            print(f"[{lang}] 번역 오류: {e}")
            return lang, original_texts 

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(translate_language, target_langs)
        
        for lang, translated_texts in results:
            key_lang = 'zh' if lang == 'zh-CN' else lang
            translated_data[key_lang] = translated_texts
            # 퍼센티지를 60% ~ 95% 사이로 분배
            update_status(f"  - {key_lang} 번역 완료!", 60 + target_langs.index(lang) * 7)

    update_status("[2/2] 최종 데이터 조립 중...", 95)
    
    subtitles = []
    for i, segment in enumerate(segments):
        texts_dict = {}
        # for lang_key in ['original', 'ko', 'en', 'ja', 'zh', 'de']:
        for lang_key in ['original', 'ko', 'en', 'ja']:
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