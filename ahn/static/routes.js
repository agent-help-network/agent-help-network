// Progressive enhancement for the task route pages: without this file the server reads whatever was typed in the site box.
// A pasted address is cut down to its hostname before the form leaves the browser, so a path or a query never goes with it.
(function () {
  var inputs = document.querySelectorAll('form input[name="site"]');
  Array.prototype.forEach.call(inputs, function (input) {
    var form = input.form;
    if (!form) return;
    form.addEventListener("submit", function () {
      var value = input.value.trim();
      if (!/[\/?#]/.test(value)) return;
      var web = /^https?:\/\//i.test(value);
      if (!web && value.indexOf("://") !== -1) return; // another scheme: the server refuses it
      try {
        input.value = new URL(web ? value : "https://" + value).hostname;
      } catch (e) {}
    });
  });
})();
