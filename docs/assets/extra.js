document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".feature-card").forEach(function (card) {
    card.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        var link = card.querySelector("a");
        if (link) {
          link.click();
        }
      }
    });
  });
});
