(function (global) {
  "use strict";

  async function copy(text) {
    if (typeof global.ClipboardJS !== "function") {
      return false;
    }

    return new Promise(function (resolve) {
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.tabIndex = -1;
      trigger.setAttribute("aria-hidden", "true");
      trigger.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
      document.body.appendChild(trigger);

      const clipboard = new global.ClipboardJS(trigger, {
        container: document.body,
        text: function () {
          return String(text);
        },
      });
      let completed = false;

      function finish(result) {
        if (completed) return;
        completed = true;
        clipboard.destroy();
        trigger.remove();
        resolve(result);
      }

      clipboard.on("success", function (event) {
        event.clearSelection();
        finish(true);
      });
      clipboard.on("error", function () {
        finish(false);
      });
      trigger.click();
      global.setTimeout(function () {
        finish(false);
      }, 1000);
    });
  }

  global.vpnClipboard = Object.freeze({ copy: copy });
})(window);
