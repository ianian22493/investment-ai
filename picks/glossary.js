// Shared glossary tooltip helper for pick pages.
// Applies data-tip attributes to labels matching the dictionary.
// CSS lives in picks.css ([data-tip] rules).
(function(){
  const GLOSSARY = {
    "R:R":       "風險報酬比 · 潛在獲利 ÷ 停損距離。1:2 = 贏 2 賠 1，越高越好",
    "β":         "Beta 值 · 相對大盤的敏感度。β=1 跟大盤同步，β>1 波動更大",
    "VIX":       "恐慌指數 · 反映美股未來 30 天預期波動。<20 平靜、>30 恐慌",
    "停損":       "跌到這個價位就出場，強制止損不猶豫",
    "停利":       "漲到這個價位獲利了結（也可視情況延後）",
    "進場區":     "建議買進的價格區間，太高就不追",
    "目標":       "預期的獲利出場價位",
    "Entry":     "進場區間 · 建議買進的價格範圍",
    "Stop":      "停損 · 跌到這出場，強制止損不猶豫",
    "Target":    "目標 · 預期的獲利出場價位",
    "Hold":      "持有期 · 預估要抱多久",
    "REGIME":    "市場情境判定 · 分成「趨勢多頭 / 震盪整理 / 趨勢空頭」等",
    "REFLECTION": "反思 · AI 每日檢視昨日決策是否成功並記取教訓",
    "confidence": "信心值 · 系統對這個判斷的把握程度，0.9 = 90% 把握",
    "空手觀望":   "系統建議今日不進場 · 通常因為風險高於預期報酬",
    "推薦出手":   "系統建議今日出手 · 找到勝率與風報比都合格的機會",
    "局部防守":   "系統建議降低倉位 · 情況不利但也不必全面撤退",
    "CONFIDENCE": "信心值 · 系統對這個判斷的把握程度，0.9 = 90% 把握",
    "SCANNER":   "篩選雷達 · 從技術面與籌碼面篩出的候選股",
    "OFF-BOOK":  "帳外註記 · 給你參考但不進入正式績效紀錄",
  };
  function prefixOf(t){ return (t||'').replace(/\s*[·:.·].*$/, '').trim(); }
  function apply(root){
    root = root || document;
    const sels = ['.kn-label','.kn-sub','.chip','.stat-label','.section-title','.badge','[data-term]'];
    root.querySelectorAll(sels.join(',')).forEach(el => {
      if(el.hasAttribute('data-tip')) return;
      const raw = (el.textContent||'').trim();
      if(GLOSSARY[raw]){ el.setAttribute('data-tip', GLOSSARY[raw]); return; }
      const p = prefixOf(raw);
      if(p && GLOSSARY[p]){ el.setAttribute('data-tip', GLOSSARY[p]); return; }
      const term = el.getAttribute('data-term');
      if(term && GLOSSARY[term]){ el.setAttribute('data-tip', GLOSSARY[term]); }
    });
  }
  document.addEventListener('DOMContentLoaded', () => apply());
  window.applyGlossary = apply;
})();
