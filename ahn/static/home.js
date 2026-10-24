// Progressive enhancement for the home page: without this file every install panel is plain, visible text.
(function () {
  var copyLabel = document.documentElement.lang === "ko" ? "복사" : "Copy";
  var doneLabel = document.documentElement.lang === "ko" ? "복사됨" : "Copied";

  function addCopy(box, source, anchor) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "home-copy";
    button.textContent = copyLabel;
    button.addEventListener("click", function () {
      var done = function () { button.textContent = doneLabel; };
      try {
        navigator.clipboard.writeText(source.textContent).then(done, function () {});
      } catch (e) {}
    });
    anchor.appendChild(button);
  }

  var card = document.getElementById("install-card");
  if (card) {
    var cardSource = card.querySelector(".home-copy-source");
    if (cardSource) {
      // The "$" prompts are drawn by CSS, so the copied text is just the two commands.
      addCopy(card, cardSource, card.querySelector(".home-install-head"));
    }
  }

  var tabs = document.querySelector("[data-home-tabs]");
  if (!tabs) return;
  var panels = Array.prototype.slice.call(tabs.querySelectorAll(".home-panel"));
  var list = document.createElement("div");
  list.className = "home-tablist";
  list.setAttribute("role", "tablist");
  var buttons = panels.map(function (panel, index) {
    var button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "tab");
    button.id = "tabbtn-" + panel.id;
    button.setAttribute("aria-controls", panel.id);
    button.textContent = panel.getAttribute("data-label");
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", button.id);
    var source = panel.querySelector(".home-copy-source");
    if (source) {
      var wrap = source.closest("pre");
      wrap.classList.add("home-has-copy");
      addCopy(panel, source, wrap);
    }
    button.addEventListener("click", function () { select(index); });
    button.addEventListener("keydown", function (event) {
      var next = event.key === "ArrowRight" ? index + 1 : event.key === "ArrowLeft" ? index - 1 : null;
      if (next === null) return;
      next = (next + buttons.length) % buttons.length;
      select(next);
      buttons[next].focus();
      event.preventDefault();
    });
    list.appendChild(button);
    return button;
  });
  function select(chosen) {
    panels.forEach(function (panel, index) {
      panel.hidden = index !== chosen;
      buttons[index].setAttribute("aria-selected", index === chosen ? "true" : "false");
      buttons[index].tabIndex = index === chosen ? 0 : -1;
    });
  }
  tabs.classList.add("home-tabs-on");
  tabs.insertBefore(list, tabs.firstChild);
  select(0);
})();
