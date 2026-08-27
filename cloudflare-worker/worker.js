/**
 * Cloudflare Email Worker - 通用 Catch-all 邮件投递脚本
 * 
 * 功能：捕获发往域名下任意邮箱 (*@onprs.online) 的所有邮件，
 * 并通过安全的 HTTPS Webhook 直投到 Onprs Email 服务器。
 */

export default {
  async email(message, env, ctx) {
    // 1. 获取发件人、收件人与原始邮件流 (RFC 822 / MIME)
    const from = message.from;
    const to = message.to;
    const rawEmail = await new Response(message.raw).text();

    // 2. 配置服务器接收端点与通信密钥
    const ingressUrl = env.INGRESS_URL || "https://mail.onprs.online/api/email-ingress";
    const ingressSecret = env.INGRESS_SECRET;

    if (!ingressSecret) {
      console.error("缺少 INGRESS_SECRET Worker Secret，邮件未投递");
      message.setReject("Email ingress is not configured");
      return;
    }

    // 3. 将邮件打包并安全投递给自建邮件服务器
    try {
      const response = await fetch(ingressUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Ingress-Secret": ingressSecret,
        },
        body: JSON.stringify({
          from: from,
          to: to,
          raw: rawEmail,
        }),
      });

      if (!response.ok) {
        const errorDetail = await response.text();
        console.error(`邮件投递失败: HTTP ${response.status} - ${errorDetail}`);
        message.setReject(`Server rejected message: HTTP ${response.status}`);
      } else {
        console.log("邮件已成功投递至 Ingress 服务");
      }
    } catch (err) {
      console.error(`网络请求异常: ${err.message}`);
      message.setReject(`Delivery exception: ${err.message}`);
    }
  },
};
