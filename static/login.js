const form = document.getElementById("loginForm");
const errorNode = document.getElementById("loginError");
const button = document.getElementById("loginButton");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorNode.textContent = "";
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: document.getElementById("loginEmail").value,
        password: document.getElementById("loginPassword").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudo iniciar sesión.");
    }
    window.location.replace(data.user?.role === "admin" ? "/admin" : "/bi");
  } catch (error) {
    errorNode.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
