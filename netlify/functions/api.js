const crypto = require("crypto");
const querystring = require("querystring");

exports.handler = async (event) => {
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, x-webhook-signature, x-webhook-timestamp",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Content-Type": "application/json"
  };

  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 200, headers, body: JSON.stringify({ ok: true }) };
  }

  const GH_PAT = (process.env.GH_PAT || "").trim();
  const GH_REPO = (process.env.GH_REPO || "").trim();
  const CF_APP_ID = (process.env.CASHFREE_APP_ID || "").trim();
  const CF_SECRET = (process.env.CASHFREE_SECRET_KEY || "").trim();
  const CF_ENV = (process.env.CASHFREE_ENV || "PRODUCTION").trim().toUpperCase();

  const CF_BASE_URL = CF_ENV === "SANDBOX"
    ? "https://sandbox.cashfree.com/pg"
    : "https://api.cashfree.com/pg";

  if (!GH_PAT || !GH_REPO) {
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: "Netlify Environment Variables GH_PAT ya GH_REPO set nahi hain!" })
    };
  }

  const ghHeaders = {
    "Authorization": `Bearer ${GH_PAT}`,
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json"
  };

  async function readGhJson(filePath) {
    const url = `https://api.github.com/repos/${GH_REPO}/contents/${filePath}?t=${Date.now()}`;
    const r = await fetch(url, { headers: ghHeaders });
    if (r.status === 404) return { data: null, sha: null };
    if (!r.ok) throw new Error(`GitHub Read Error (${r.status})`);
    const fileObj = await r.json();
    const decoded = Buffer.from(fileObj.content, "base64").toString("utf-8");
    return { data: JSON.parse(decoded), sha: fileObj.sha };
  }

  async function writeGhJson(filePath, dataObj, commitMsg, sha = null) {
    const url = `https://api.github.com/repos/${GH_REPO}/contents/${filePath}`;
    const contentB64 = Buffer.from(JSON.stringify(dataObj, null, 2), "utf-8").toString("base64");
    const body = {
      message: `${commitMsg} [skip ci]`,
      content: contentB64
    };
    if (sha) body.sha = sha;
    const r = await fetch(url, { method: "PUT", headers: ghHeaders, body: JSON.stringify(body) });
    if (!r.ok) {
      const errTxt = await r.text();
      throw new Error(`GitHub Write Error: ${errTxt}`);
    }
  }

  function hashStr(str) {
    return crypto.createHash("sha256").update(str).digest("hex");
  }

  function createAuthToken(email) {
    const clean = email.trim().toLowerCase();
    const sig = crypto.createHmac("sha256", GH_PAT).update(clean).digest("hex");
    return `${Buffer.from(clean).toString("base64")}.${sig}`;
  }

  function verifyAuthToken(token) {
    if (!token || !token.includes(".")) return null;
    const [b64Email, sig] = token.split(".");
    const email = Buffer.from(b64Email, "base64").toString("utf-8");
    const expectedSig = crypto.createHmac("sha256", GH_PAT).update(email).digest("hex");
    return sig === expectedSig ? email : null;
  }

  function isSuspiciousOrDisposableEmail(email) {
    const parts = email.split("@");
    if (parts.length !== 2) return true;
    const [local, domain] = parts;
    const blockedDomains = new Set([
      "tempmail.com", "10minutemail.com", "guerrillamail.com", "yopmail.com",
      "mailinator.com", "trashmail.com", "sharklasers.com", "getnada.com", "temp-mail.org"
    ]);
    if (blockedDomains.has(domain)) return true;
    if (local.length < 4 || /^(test|fake|user|admin|abcd|asdf|qwerty|1234)/i.test(local)) return true;
    // Block repeated gibberish like aaaaa@gmail.com
    if (/^(.)\1{3,}/.test(local)) return true;
    return false;
  }

  function findEmailInObject(obj) {
    if (!obj) return "";
    if (typeof obj === "string") {
      const s = obj.trim();
      if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s)) return s.toLowerCase();
      return "";
    }
    if (typeof obj === "object") {
      for (const k of Object.keys(obj)) {
        const found = findEmailInObject(obj[k]);
        if (found) return found;
      }
    }
    return "";
  }

  let body = {};
  if (event.body) {
    const rawBody = event.isBase64Encoded
      ? Buffer.from(event.body, "base64").toString("utf-8")
      : event.body;
    try {
      body = JSON.parse(rawBody);
    } catch (e) {
      try {
        body = querystring.parse(rawBody);
      } catch (e2) {
        body = {};
      }
    }
  }

  try {
    const action = event.queryStringParameters?.action || "";

    // =========================================================
    // 1. SIGN UP / LOGIN (WITH IP + DEVICE ANTI-ABUSE LOCK!)
    // =========================================================
    if (action === "auth" && event.httpMethod === "POST") {
      const email = (body.email || "").trim().toLowerCase();
      const password = (body.password || "").trim();
      const name = (body.name || email.split("@")[0] || "Creator").trim();
      const deviceId = (body.device_id || "").trim();

      const clientIp = (
        event.headers["x-nf-client-connection-ip"] ||
        event.headers["client-ip"] ||
        (event.headers["x-forwarded-for"] || "").split(",")[0] ||
        "unknown_ip"
      ).trim();

      if (!email.includes("@") || password.length < 4) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "Sahi Email aur kam se kam 4 akshar ka Password daalo!" }) };
      }

      if (isSuspiciousOrDisposableEmail(email)) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "Fake ya temporary email allowed nahi hai! Apna asli personal Email daalo." }) };
      }

      const userFile = `db/users/${hashStr(email).slice(0, 20)}.json`;
      const { data: existingUser } = await readGhJson(userFile);
      const passHash = hashStr(password + GH_PAT);

      if (!existingUser) {
        // Check if this IP or Device has ALREADY claimed the 10 Free Tokens!
        const ipClaimFile = `db/claims/ip_${hashStr(clientIp).slice(0, 16)}.json`;
        const devClaimFile = deviceId ? `db/claims/dev_${hashStr(deviceId).slice(0, 16)}.json` : null;

        const { data: ipClaimed } = await readGhJson(ipClaimFile);
        const { data: devClaimed } = devClaimFile ? await readGhJson(devClaimFile) : { data: null };

        const alreadyClaimedFreeTokens = Boolean(ipClaimed || devClaimed);
        const startingTokens = alreadyClaimedFreeTokens ? 0 : 10;

        const newUser = {
          email,
          name,
          passHash,
          tokens: startingTokens,
          created_at: Date.now()
        };
        await writeGhJson(userFile, newUser, `New user signup: ${email}`);

        if (!alreadyClaimedFreeTokens) {
          await writeGhJson(ipClaimFile, { email, claimed_at: Date.now() }, `IP claim lock`);
          if (devClaimFile) {
            await writeGhJson(devClaimFile, { email, claimed_at: Date.now() }, `Device claim lock`);
          }
        }

        return {
          statusCode: 200,
          headers,
          body: JSON.stringify({
            auth_token: createAuthToken(email),
            email,
            name,
            tokens: startingTokens,
            is_new: true,
            message: alreadyClaimedFreeTokens
              ? "⚠️ Is device/network par 10 Free Tokens pehle hi dusre account par mil chuke hain! Is naye account mein 0 Tokens hain. Recharge (₹1 = 5 Tokens) karke use karein."
              : "🎉 Welcome! Aapko 10 FREE Tokens mile hain!"
          })
        };
      } else {
        if (existingUser.passHash !== passHash) {
          return { statusCode: 401, headers, body: JSON.stringify({ error: "Galat Password! Sahi password daalo." }) };
        }
        return {
          statusCode: 200,
          headers,
          body: JSON.stringify({
            auth_token: createAuthToken(email),
            email: existingUser.email,
            name: existingUser.name,
            tokens: existingUser.tokens,
            is_new: false,
            message: "Welcome back!"
          })
        };
      }
    }

    // =========================================================
    // 2. GET CURRENT USER PROFILE & LIVE TOKEN BALANCE
    // =========================================================
    if (action === "me") {
      const email = verifyAuthToken(event.headers.authorization || event.queryStringParameters?.auth_token);
      if (!email) return { statusCode: 401, headers, body: JSON.stringify({ error: "Unauthorized" }) };

      const userFile = `db/users/${hashStr(email).slice(0, 20)}.json`;
      const { data: user } = await readGhJson(userFile);
      if (!user) return { statusCode: 404, headers, body: JSON.stringify({ error: "User not found" }) };

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({ email: user.email, name: user.name, tokens: user.tokens })
      };
    }

    // =========================================================
    // 3. BACKUP CLAIM BY CASHFREE ORDER ID (e.g. CFPay_...)
    // =========================================================
    if (action === "claim_order" && event.httpMethod === "POST") {
      const email = verifyAuthToken(body.auth_token);
      const orderId = (body.order_id || "").trim();
      if (!email || !orderId) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "Apna Cashfree Order ID (CFPay_...) daalo!" }) };
      }

      const safeOrderKey = orderId.replace(/[^a-zA-Z0-9_-]/g, "");
      const orderFile = `db/orders/${safeOrderKey}.json`;
      const { data: existingOrder } = await readGhJson(orderFile);
      if (existingOrder) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "Yeh Order ID pehle hi claim ho chuka hai!" }) };
      }

      const cfRes = await fetch(`${CF_BASE_URL}/orders/${encodeURIComponent(orderId)}`, {
        method: "GET",
        headers: {
          "x-client-id": CF_APP_ID,
          "x-client-secret": CF_SECRET,
          "x-api-version": "2023-08-01",
          "Accept": "application/json"
        }
      });
      const cfData = await cfRes.json();

      if (!cfRes.ok || (cfData.order_status || "").toUpperCase() !== "PAID") {
        return {
          statusCode: 400,
          headers,
          body: JSON.stringify({ error: `Order verify nahi hua (Status: ${cfData.order_status || cfData.message || "NOT FOUND"})` })
        };
      }

      const paidAmount = Math.max(1, Math.floor(Number(cfData.order_amount || 1)));
      const tokensEarned = paidAmount * 5; // ₹1 = 5 Tokens

      const userFile = `db/users/${hashStr(email).slice(0, 20)}.json`;
      const { data: user, sha: userSha } = await readGhJson(userFile);
      if (!user) throw new Error("User account missing!");

      user.tokens = (Number(user.tokens) || 0) + tokensEarned;
      await writeGhJson(userFile, user, `Claimed ${tokensEarned} tokens via Order ${orderId}`, userSha);
      await writeGhJson(orderFile, {
        order_id: orderId,
        email,
        amount_inr: paidAmount,
        tokens_added: tokensEarned,
        new_balance: user.tokens,
        paid_at: Date.now()
      }, `Order ${orderId} claimed`);

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          success: true,
          tokens_added: tokensEarned,
          tokens: user.tokens,
          message: `🎉 Verified! +${tokensEarned} Tokens aapke wallet mein jud gaye!`
        })
      };
    }

    // =========================================================
    // 4. DEDUCT TOKENS & START E-BOOK GENERATION ON GITHUB
    // =========================================================
    if (action === "start" && event.httpMethod === "POST") {
      const email = verifyAuthToken(body.auth_token);
      if (!email) {
        return { statusCode: 401, headers, body: JSON.stringify({ error: "E-Book banane ke liye pehle Sign-In karo!" }) };
      }

      const volume = (body.volume || "standard").toLowerCase();
      const tokenCost = volume === "quick" ? 2 : (volume === "mega" ? 10 : 5);

      const userFile = `db/users/${hashStr(email).slice(0, 20)}.json`;
      const { data: user, sha: userSha } = await readGhJson(userFile);

      if (!user || user.tokens < tokenCost) {
        return {
          statusCode: 402,
          headers,
          body: JSON.stringify({
            error: `Insufficient Tokens! Is book ke liye ${tokenCost} Tokens chahiye, par aapke paas ${user ? user.tokens : 0} Tokens hain.`,
            need_recharge: true
          })
        };
      }

      user.tokens -= tokenCost;
      await writeGhJson(userFile, user, `Used ${tokenCost} tokens for ebook`, userSha);

      const jobId = `job_${Date.now()}`;
      const dispatchUrl = `https://api.github.com/repos/${GH_REPO}/actions/workflows/generate_ebook.yml/dispatches`;
      const resp = await fetch(dispatchUrl, {
        method: "POST",
        headers: ghHeaders,
        body: JSON.stringify({
          ref: "main",
          inputs: {
            job_id: jobId,
            payload_json: JSON.stringify(body)
          }
        })
      });

      if (!resp.ok) {
        const { data: uRef, sha: sRef } = await readGhJson(userFile);
        if (uRef) {
          uRef.tokens += tokenCost;
          await writeGhJson(userFile, uRef, "Refund tokens on dispatch error", sRef);
        }
        const errText = await resp.text();
        return { statusCode: resp.status, headers, body: JSON.stringify({ error: `GitHub Workflow Error: ${errText}` }) };
      }

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          job_id: jobId,
          tokens_remaining: user.tokens,
          progress: 3,
          message: `⚡ ${tokenCost} Tokens Deducted! 24/7 Cloud Engine Started...`
        })
      };
    }

    // =========================================================
    // 5. POLL LIVE E-BOOK PROGRESS STATUS
    // =========================================================
    if (action === "status" && event.queryStringParameters?.job_id) {
      const jobId = event.queryStringParameters.job_id;
      const { data: statusData } = await readGhJson(`status/${jobId}.json`);
      if (!statusData) {
        return {
          statusCode: 200,
          headers,
          body: JSON.stringify({ progress: 5, message: "Booting 24/7 GitHub Cloud Runner...", done: false })
        };
      }
      return { statusCode: 200, headers, body: JSON.stringify(statusData) };
    }

    // =========================================================
    // 6. AUTOMATIC CASHFREE WEBHOOK HANDLER (INSTANT CREDIT!)
    // =========================================================
    const wfData = body.data || body || {};
    const orderObj = wfData.order || {};
    const formObj = wfData.form || {};
    const paymentObj = wfData.payment || {};
    const custObj =
      wfData.customer_details ||
      orderObj.customer_details ||
      formObj.customer_details ||
      body.customer_details ||
      {};

    const rawStatus = String(
      paymentObj.payment_status ||
      orderObj.order_status ||
      formObj.order_status ||
      wfData.order_status ||
      wfData.txStatus ||
      body.order_status ||
      "SUCCESS"
    ).toUpperCase();

    const rawEmail = (
      custObj.customer_email ||
      orderObj.customer_email ||
      formObj.customer_email ||
      wfData.customer_email ||
      wfData.customerEmail ||
      findEmailInObject(body) ||
      ""
    ).trim().toLowerCase();

    const rawAmount = Math.floor(Number(
      paymentObj.payment_amount ||
      orderObj.order_amount ||
      orderObj.transaction_amount ||
      formObj.order_amount ||
      formObj.cf_amount ||
      wfData.order_amount ||
      wfData.orderAmount ||
      body.order_amount ||
      body.amount ||
      0
    ));

    const rawTxId = String(
      orderObj.order_id ||
      paymentObj.cf_payment_id ||
      formObj.order_id ||
      wfData.order_id ||
      wfData.referenceId ||
      `tx_${Date.now()}`
    ).replace(/[^a-zA-Z0-9_-]/g, "");

    if ((rawStatus === "SUCCESS" || rawStatus === "PAID") && rawEmail.includes("@") && rawAmount >= 1) {
      const orderFile = `db/orders/${rawTxId}.json`;
      const { data: existingOrder } = await readGhJson(orderFile);

      if (!existingOrder) {
        const tokensEarned = rawAmount * 5; // ₹1 = 5 Tokens
        const userFile = `db/users/${hashStr(rawEmail).slice(0, 20)}.json`;
        const { data: user, sha: userSha } = await readGhJson(userFile);

        if (user) {
          user.tokens = (Number(user.tokens) || 0) + tokensEarned;
          await writeGhJson(userFile, user, `Webhook credited ${tokensEarned} tokens to ${rawEmail}`, userSha);
          await writeGhJson(orderFile, {
            order_id: rawTxId,
            email: rawEmail,
            amount_inr: rawAmount,
            tokens_added: tokensEarned,
            new_balance: user.tokens,
            paid_at: Date.now()
          }, `Webhook order ${rawTxId}`);
        }
      }
    }

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ status: "ok", webhook_verified: true })
    };

  } catch (err) {
    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ status: "ok", note: err.message })
    };
  }
};
