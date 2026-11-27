# Hamro Chitwan – Automated Single-News Video

## मुख्य सेटिङ
- 1080x1350 (4:5), 30 FPS
- प्रत्येक भिडियोमा एउटा मुख्य समाचार
- पुरुष नेपाली TTS (ne-NP-SagarNeural; उपलब्धता region/provider अनुसार फरक पर्न सक्छ)
- स्रोत: Onlinekhabar र eKantipur RSS (फिड URL परिवर्तन हुन सक्छ)
- दैनिक 08:00, 14:00, 17:00, 21:00 Nepal time
- Facebook Page Reels मा स्वचालित प्रकाशन

## 1. Repository मा फाइल राख्नुहोस्
सबै फाइलको संरचना जस्ताको तस्तै GitHub मा upload गर्नुहोस्।

## 2. Assets तयार गर्नुहोस्
`assets/` मा यी फाइल राख्नुहोस्:
- `presenter.png`: तपाईंलाई प्रयोग गर्ने अधिकार भएको transparent male presenter image
- `logo.png`: Hamro Chitwan logo (optional)
- `music.mp3`: royalty-free / licensed background music (optional)
- `fonts/` मा Noto Sans Devanagari को Bold र Regular TTF:
  - `NotoSansDevanagari-Bold.ttf`
  - `NotoSansDevanagari-Regular.ttf`

यदि font फाइल छैन भने system fallback font चल्न सक्छ तर नेपाली rendering बिग्रन सक्छ।

## 3. GitHub Secrets
Repository → Settings → Secrets and variables → Actions → New repository secret:
- `FB_PAGE_ID`
- `FB_PAGE_ACCESS_TOKEN`
- `PIXABAY_API_KEY` (optional)

Page token मा Reels publish गर्न आवश्यक Meta permissions हुनुपर्छ। Token/page access setup Meta Developer Dashboard मा verify गर्नुहोस्।

## 4. Manual test
Actions → Hamro Chitwan Automated News Videos → Run workflow → `dry_run=1`.
यो अवस्थामा भिडियो बनाउँछ, Facebook मा पोस्ट गर्दैन।
पहिलो परीक्षणमा `DRY_RUN=1` नै राख्नुहोस्।

## 5. सावधानी / Production checklist
- RSS endpoints Onlinekhabar/eKantipur ले परिवर्तन गर्न सक्छन्; feed fetch fail भए logs मा देखिन्छ।
- RSS summary नै voice script मा प्रयोग हुन्छ; स्रोतमा नभएको तथ्य थपिँदैन।
- Google Images बाट सीधै scrape गरिएको छैन। Google result मा भेटिएको image प्रयोग गर्नुअघि license/permission प्रमाणित गर्नुपर्छ।
- Pixabay API का terms पालना गर्नुहोस्; article सँग सम्बन्धित नभएको stock photo प्रयोग नगर्नुहोस्।
- Publisher को robots.txt, terms, copyright र paywall पालना गर्नुहोस्।
- Original article photo उपलब्ध नभए Pixabay बाट keyword search हुन्छ; relevance मानवीय रूपमा जाँच्नुहोस्।
- Facebook Graph API version/permissions र Reels upload requirements परिवर्तन हुन सक्छन्।
- Workflow schedule GitHub Actions मा केही मिनेट ढिलो चल्न सक्छ।
- `presenter.png` र `logo.png` तपाईंले उपलब्ध गराउनु पर्ने assets हुन्। यो starter code reference को color/layout direction पछ्याउँछ; uploaded video को exact frame-by-frame clone होइन।


## 6. 4:5 Facebook publishing
यो project ले 1080x1350 (4:5) फाइललाई Page Videos endpoint बाट publish गर्छ। Reels-specific endpoint प्रयोग गरिएको छैन, किनकि त्यसको format requirement फरक हुन सक्छ। Facebook ले Page video लाई कसरी surface गर्छ भन्ने कुरा Page/account rollout मा निर्भर हुन्छ।
