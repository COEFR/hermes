// RELOJ en Cloudflare Workers (gratis, sin tarjeta): dispara el workflow del bot cada 5 minutos.
//
// Cómo se usa (5 minutos, todo desde el panel de Cloudflare):
//  1. dash.cloudflare.com → Workers & Pages → Create → Worker (nombre: reloj-bot-senales)
//  2. Reemplazá el código de ejemplo por TODO este archivo y dale Deploy
//  3. Settings → Variables and Secrets → Add → tipo Secret, nombre GITHUB_TOKEN,
//     valor: tu token de GitHub (el de permiso Actions: Read and write)
//     Y otra variable normal: REPO = COEFR/Hermes-Signal-bot
//  4. Settings → Triggers → Cron Triggers → Add → */5 * * * *
//
// Con eso el reloj vive en Cloudflare y no depende de ninguna máquina tuya.
export default {
  async scheduled(event, env, ctx) {
    const url = `https://api.github.com/repos/${env.REPO}/actions/workflows/bot.yml/dispatches`;
    const r = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "reloj-bot-senales",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    console.log(`disparo del ciclo: HTTP ${r.status}`);
  },
};
