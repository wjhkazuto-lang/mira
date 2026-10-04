// 在 Mira 桌面窗口里时给 <html> 加 in-app：标题栏合进页面，顶部给红黄绿按钮留位置
(function () {
  const KEY = "mira-in-app";
  function add() {
    document.documentElement.classList.add("in-app");
    try { sessionStorage.setItem(KEY, "1"); } catch (e) {}
  }
  // pywebview 每次页面加载完才注入 window.pywebview；同一窗口里换页时先凭记号加上，免得页面跳一下
  try { if (sessionStorage.getItem(KEY)) add(); } catch (e) {}
  if (window.pywebview) add();
  else window.addEventListener("pywebviewready", add);
})();
