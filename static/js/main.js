// Mert Ders Takip - Modern UI/UX Core Script
document.addEventListener("DOMContentLoaded", function() {
    // Auto-dismiss alerts after 4 seconds
    const alerts = document.querySelectorAll('.alert-dismissible');
    alerts.forEach(function(alertEl) {
        setTimeout(function() {
            try {
                const bsAlert = new bootstrap.Alert(alertEl);
                bsAlert.close();
            } catch(e) {}
        }, 4000);
    });

    // Initialize tooltips if Bootstrap Tooltip is available
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
});
