import { NextRequest, NextResponse } from "next/server";
import { signPayment, type PaymentRequirements } from "@/lib/x402";

const LLM_ENDPOINT = "https://llm.opengradient.ai/v1/chat/completions";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { model, messages, max_tokens, temperature, settlement } = body as {
      model?: string;
      messages: { role: string; content: string }[];
      max_tokens?: number;
      temperature?: number;
      settlement?: string;
    };

    if (!messages || messages.length === 0) {
      return NextResponse.json(
        { error: "messages is required" },
        { status: 400 }
      );
    }

    const llmModel = model || "openai/gpt-4o";
    const llmBody = JSON.stringify({
      model: llmModel,
      messages,
      max_tokens: max_tokens ?? 500,
      temperature: temperature ?? 0.7,
    });

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (settlement) {
      headers["X-SETTLEMENT-TYPE"] = settlement;
    }

    // Step 1: initial request → expect 402 Payment Required
    const firstRes = await fetch(LLM_ENDPOINT, {
      method: "POST",
      headers,
      body: llmBody,
    });

    if (firstRes.status !== 402) {
      // Might be 200 (no payment needed?) or error
      const data = await firstRes.json().catch(() => ({}));
      if (firstRes.ok) return NextResponse.json(data);
      return NextResponse.json(
        { error: data?.error?.message || `Unexpected status ${firstRes.status}`, raw: data },
        { status: firstRes.status }
      );
    }

    // Step 2: parse payment requirements from header
    const payReqHeader = firstRes.headers.get("X-Payment-Required") ||
      firstRes.headers.get("x-payment-required");
    if (!payReqHeader) {
      return NextResponse.json(
        { error: "402 but no X-Payment-Required header" },
        { status: 502 }
      );
    }

    let requirements: PaymentRequirements;
    try {
      requirements = JSON.parse(
        Buffer.from(payReqHeader, "base64").toString("utf-8")
      );
    } catch {
      return NextResponse.json(
        { error: "Failed to decode payment requirements" },
        { status: 502 }
      );
    }

    // Step 3: sign payment with server private key
    const xPayment = await signPayment(requirements);

    // Step 4: resubmit with X-PAYMENT header
    const secondRes = await fetch(LLM_ENDPOINT, {
      method: "POST",
      headers: {
        ...headers,
        "X-PAYMENT": xPayment,
      },
      body: llmBody,
    });

    const result = await secondRes.json().catch(() => ({}));

    if (!secondRes.ok) {
      return NextResponse.json(
        { error: result?.error?.message || `LLM error ${secondRes.status}`, raw: result },
        { status: secondRes.status }
      );
    }

    // Include payment receipt if present
    const receiptHeader = secondRes.headers.get("X-Payment-Response") ||
      secondRes.headers.get("x-payment-response");
    let receipt: unknown = null;
    if (receiptHeader) {
      try {
        receipt = JSON.parse(
          Buffer.from(receiptHeader, "base64").toString("utf-8")
        );
      } catch { /* ignore */ }
    }

    return NextResponse.json({ ...result, _paymentReceipt: receipt });
  } catch (err: any) {
    console.error("[api/chat] error:", err);
    return NextResponse.json(
      { error: err?.message || "Internal error" },
      { status: 500 }
    );
  }
}
