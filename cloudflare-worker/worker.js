/**
 * Cloudflare Email Worker：将 Catch-all 邮件转发至 Ingress HTTPS 接口。
 */

export default {
  async email(message, env) {
    const from = message.from;
    const to = message.to;
    const rawEmail = await new Response(message.raw).text();
    const ingressUrl = env.INGRESS_URL || "https://mail.onprs.online/api/email-ingress";
    const ingressSecret = env.INGRESS_SECRET;

    if (!ingressSecret) {
      console.error("缺少 Ingress 密钥，邮件未转发");
      message.setReject("Email ingress is not configured");
      return;
    }

    try {
      const response = await fetch(ingressUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Ingress-Secret": ingressSecret,
        },
        body: JSON.stringify({
          from,
          to,
          raw: rawEmail,
        }),
      });

      if (!response.ok) {
        const errorDetail = (await response.text()).slice(0, 500);
        console.error(`邮件转发失败：HTTP ${response.status} - ${errorDetail}`);
        message.setReject(`Server rejected message: HTTP ${response.status}`);
        return;
      }

      console.log("邮件已由 Ingress 接收");
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error(`邮件转发请求失败：${errorMessage}`);
      message.setReject("Email ingress is temporarily unavailable");
    }
  },
};
