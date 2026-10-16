
document.addEventListener("DOMContentLoaded", () => {
    const selectAllButton = document.getElementById("select-all");

    if (selectAllButton) {
        selectAllButton.addEventListener("click", () => {
            const checkboxes = Array.from(
                document.querySelectorAll(
                    'input[name="participants"]'
                )
            );

            const allSelected = checkboxes.every(
                checkbox => checkbox.checked
            );

            checkboxes.forEach(checkbox => {
                checkbox.checked = !allSelected;
            });

            selectAllButton.textContent = allSelected
                ? "Select all"
                : "Deselect all";
        });
    }

    document.querySelectorAll(".delete-form").forEach(form => {
        form.addEventListener("submit", event => {
            const confirmed = window.confirm(
                "Are you sure you want to delete this expense?"
            );

            if (!confirmed) {
                event.preventDefault();
            }
        });
    });
});
