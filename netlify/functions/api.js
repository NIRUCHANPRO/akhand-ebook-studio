const crypto = require("crypto");

exports.handler = async (event) => {
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Content-Type": "application/json"
  };

  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 200, headers, body: "" };
  }

  const GH_PAT = process.env.GH_PAT;
  const GH_REPO = process.env.GH_REPO;
  const CF_APP_ID = process.env.CASHFREE_APP_ID;
  const CF_SECRET = process.env.CASHFREE_SECRET_KEY;
  const CF_ENV = (process.env.CASHFREE_ENV || "PRODUCTION").toUpperCase();

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

  // --- HELPER: Read & Write JSON in GitHub Repo (Zero External DB Cost!) ---
  async function readGhJson(filePath) {
    const url = `https://api.github.com/repos/${GH_REPO}/contents/${filePath}`;
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

  try {
    const action = event.queryStringParameters?.action || "status";
    const body = event.body ? JSON.parse(event.body) : {};

    // =========================================================
    // 1. SIGN UP / LOGIN (GIVES 10 FREE TOKENS TO NEW USERS!)
    // =========================================================
    if (action === "auth" && event.httpMethod === "POST") {
      const email = (body.email || "").trim().toLowerCase();
      const password = (body.password || "").trim();
      const name = (body.name || email.split("@")[0] || "Creator").trim();

      if (!email.includes("@") || password.length < 4) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "Sahi Email aur kam se kam 4 akshar ka Password daalo!" }) };
      }

      const userFile = `db/users/${hashStr(email).slice(0, 20)}.json`;
      const { data: existingUser } = await readGhJson(userFile);
      const passHash = hashStr(password + GH_PAT);

      if (!existingUser) {
        // New User -> Grant 10 FREE Welcome Tokens!
        const newUser = {
          email,
          name,
          passHash,
          tokens: 10,
          created_at: Date.now()
        };
        await writeGhJson(userFile, newUser, `New user signup: ${email}`);
        return {
          statusCode: 200,
          headers,
          body: JSON.stringify({
            auth_token: createAuthToken(email),
            email,
            name,
            tokens: 10,
            is_new: true,
            message: "🎉 Welcome! Aapko 10 FREE Tokens mile hain!"
          })
        };
      } else {
        // Existing User -> Check Password
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
    // 2. GET CURRENT USER PROFILE & TOKEN BALANCE
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
    // 3. CREATE CASHFREE ORDER (₹1 = 5 TOKENS)
    // =========================================================
    if (action === "create_order" && event.httpMethod === "POST") {
      const email = verifyAuthToken(body.auth_token);
      if (!email) {
        return { statusCode: 401, headers, body: JSON.stringify({ error: "Pehle Sign-In karo!" }) };
      }
      if (!CF_APP_ID || !CF_SECRET) {
        return { statusCode: 500, headers, body: JSON.stringify({ error: "Netlify mein CASHFREE_APP_ID aur CASHFREE_SECRET_KEY set nahi hain!" }) };
      }

      const amountInr = Math.max(1, Math.floor(Number(body.amount_inr || 1)));
      const tokensToCredit = amountInr * 5; 
      const orderId = `akhand_${Date.now()}_${Math.floor(Math.random() * 1000)}`;

      const cfPayload = {
        order_id: orderId,
        order_amount: amountInr,
        order_currency: "INR",
        customer_details: {
          customer_id: hashStr(email).slice(0, 16),
          customer_email: email,
          customer_phone: body.phone || "9999999999",
          customer_name: body.name || email.split("@")[0]
        },
        order_meta: {
          return_url: "https://automaticebookmaker.netlify.app"
        }
      };

      console.log("Calling Cashfree API at:", `${CF_BASE_URL}/orders`);
      
      const cfRes = await fetch(`${CF_BASE_URL}/orders`, {
        method: "POST",
        headers: {
          "x-client-id": CF_APP_ID.trim(),
          "x-client-secret": CF_SECRET.trim(),
          "x-api-version": "2023-08-01",
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify(cfPayload)
      });

      const cfData = await cfRes.json();
      if (!cfRes.ok) {
        console.error("Cashfree API Error Response:", cfData);
        return { 
          statusCode: cfRes.status, 
          headers, 
          body: JSON.stringify({ error: `Cashfree Error: ${cfData.message || JSON.stringify(cfData)}` }) 
        };
      }

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          order_id: cfData.order_id,
          payment_session_id: cfData.payment_session_id,
          amount_inr: amountInr,
          tokens_to_add: tokensToCredit,
          cf_env: CF_ENV === "SANDBOX" ? "sandbox" : "production"
        })
      };
    }

    // =========================================================
    // 4. VERIFY CASHFREE PAYMENT & ADD TOKENS (ANTI-FRAUD LOCK)
    // =========================================================
    if (action === "verify_order" && event.httpMethod === "POST") {
      const email = verifyAuthToken(body.auth_token);
      const orderId = (body.order_id || "").trim();
      if (!email || !orderId) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "Invalid request" }) };
      }

      // Check if order was already credited
      const orderFile = `db/orders/${orderId}.json`;
      const { data: existingOrder } = await readGhJson(orderFile);
      if (existingOrder) {
        return { statusCode: 200, headers, body: JSON.stringify({ already_credited: true, tokens: existingOrder.new_balance }) };
      }

      // Verify payment status directly from Cashfree Server
      const cfRes = await fetch(`${CF_BASE_URL}/orders/${orderId}`, {
        method: "GET",
        headers: {
          "x-client-id": CF_APP_ID,
          "x-client-secret": CF_SECRET,
          "x-api-version": "2023-08-01"
        }
      });
      const cfData = await cfRes.json();

      if (cfData.order_status !== "PAID") {
        return {
          statusCode: 400,
          headers,
          body: JSON.stringify({ error: `Payment abhi complete nahi hua hai (Status: ${cfData.order_status})` })
        };
      }

      const paidAmount = Math.floor(Number(cfData.order_amount || 0));
      const tokensEarned = paidAmount * 5; // ₹1 = 5 Tokens

      const userFile = `db/users/${hashStr(email).slice(0, 20)}.json`;
      const { data: user, sha: userSha } = await readGhJson(userFile);
      if (!user) throw new Error("User account missing!");

      user.tokens = (Number(user.tokens) || 0) + tokensEarned;
      await writeGhJson(userFile, user, `Credited ${tokensEarned} tokens to ${email}`, userSha);

      await writeGhJson(orderFile, {
        order_id: orderId,
        email,
        amount_inr: paidAmount,
        tokens_added: tokensEarned,
        new_balance: user.tokens,
        paid_at: Date.now()
      }, `Order ${orderId} paid`);

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          success: true,
          tokens_added: tokensEarned,
          tokens: user.tokens,
          message: `🎉 Payment Successful! +${tokensEarned} Tokens aapke wallet mein jud gaye!`
        })
      };
    }

    // =========================================================
    // 5. DEDUCT TOKENS & START E-BOOK GENERATION ON GITHUB
    // =========================================================
    if (action === "start" && event.httpMethod === "POST") {
      const email = verifyAuthToken(body.auth_token);
      if (!email) {
        return { statusCode: 401, headers, body: JSON.stringify({ error: "E-Book banane ke liye pehle Sign-In karo (Sign-In par 10 Tokens FREE milenge)!" }) };
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
            error: `Insufficient Tokens! Is book ke liye ${tokenCost} Tokens chahiye, par aapke paas ${user ? user.tokens : 0} Tokens hain. Upar '+ Buy Tokens (₹1 = 5 Tokens)' se recharge karo!`,
            need_recharge: true
          })
        };
      }

      // Deduct Tokens before dispatching job
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
        // Refund tokens if GitHub dispatch failed
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
    // 6. POLL LIVE E-BOOK PROGRESS STATUS
    // =========================================================
    if (action === "status") {
      const jobId = event.queryStringParameters?.job_id;
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

    return { statusCode: 400, headers, body: JSON.stringify({ error: "Unknown action" }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
