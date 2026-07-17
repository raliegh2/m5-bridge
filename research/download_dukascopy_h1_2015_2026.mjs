import fs from "node:fs/promises";
import path from "node:path";
import dukascopyPackage from "dukascopy-node";

const getHistoricalRates =
  dukascopyPackage.getHistoricalRates ??
  dukascopyPackage.default?.getHistoricalRates;
if (typeof getHistoricalRates !== "function") {
  throw new Error(`dukascopy-node does not expose getHistoricalRates; exports: ${Object.keys(dukascopyPackage)}`);
}

const symbols = ["gbpusd", "eurusd", "gbpjpy", "audusd", "usdjpy"];
const sides = ["bid", "ask"];
const from = new Date(process.env.DUKASCOPY_FROM || "2015-01-01T00:00:00Z");
const to = new Date(process.env.DUKASCOPY_TO || "2026-07-17T00:00:00Z");
const outputDir = process.env.DUKASCOPY_OUT || "research/dukascopy_2016_2026_data";

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isoTime(value) {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number") {
    const milliseconds = value < 100000000000 ? value * 1000 : value;
    return new Date(milliseconds).toISOString();
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`Unsupported Dukascopy timestamp: ${String(value)}`);
  }
  return parsed.toISOString();
}

function normalize(row) {
  if (Array.isArray(row)) {
    if (row.length < 5) throw new Error(`Unexpected Dukascopy array row: ${JSON.stringify(row)}`);
    return {
      time: isoTime(row[0]),
      open: number(row[1]),
      high: number(row[2]),
      low: number(row[3]),
      close: number(row[4]),
      tick_volume: number(row[5] ?? 0),
    };
  }
  if (row && typeof row === "object") {
    return {
      time: isoTime(row.timestamp ?? row.time ?? row.date ?? row.datetime),
      open: number(row.open ?? row.o),
      high: number(row.high ?? row.h),
      low: number(row.low ?? row.l),
      close: number(row.close ?? row.c),
      tick_volume: number(row.volume ?? row.volumes ?? row.tick_volume ?? row.v ?? 0),
    };
  }
  throw new Error(`Unexpected Dukascopy row type: ${typeof row}`);
}

function csv(rows) {
  const header = "time,open,high,low,close,tick_volume\n";
  const body = rows.map((row) => [
    row.time,
    row.open,
    row.high,
    row.low,
    row.close,
    row.tick_volume,
  ].join(",")).join("\n");
  return `${header}${body}\n`;
}

function yearlyChunks(start, end) {
  const chunks = [];
  let cursor = new Date(start);
  while (cursor < end) {
    const nextYear = new Date(Date.UTC(cursor.getUTCFullYear() + 1, 0, 1));
    const chunkEnd = nextYear < end ? nextYear : new Date(end);
    chunks.push({ from: new Date(cursor), to: chunkEnd });
    cursor = chunkEnd;
  }
  return chunks;
}

async function fetchChunk(symbol, side, dates) {
  let lastError;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      const data = await getHistoricalRates({
        instrument: symbol,
        dates,
        timeframe: "h1",
        priceType: side,
        format: "array",
        volumes: true,
        ignoreFlats: true,
        batchSize: 20,
      });
      if (!Array.isArray(data)) {
        throw new Error(`expected an array, received ${typeof data}`);
      }
      return data;
    } catch (error) {
      lastError = error;
      console.error(`${symbol}/${side} ${dates.from.toISOString()} attempt ${attempt} failed:`, error);
      await new Promise((resolve) => setTimeout(resolve, attempt * 2500));
    }
  }
  throw lastError;
}

async function download(symbol, side) {
  const collected = [];
  for (const dates of yearlyChunks(from, to)) {
    console.log(`  ${dates.from.toISOString().slice(0, 10)} to ${dates.to.toISOString().slice(0, 10)}`);
    const rows = await fetchChunk(symbol, side, dates);
    collected.push(...rows);
  }
  const normalized = collected
    .map(normalize)
    .filter((row) => [row.open, row.high, row.low, row.close].every(Number.isFinite))
    .sort((a, b) => a.time.localeCompare(b.time));
  const unique = [];
  let previous = null;
  for (const row of normalized) {
    if (row.time === previous) continue;
    unique.push(row);
    previous = row.time;
  }
  if (unique.length < 50000) {
    throw new Error(`${symbol}/${side}: only ${unique.length} H1 bars were returned`);
  }
  const file = path.join(outputDir, `${symbol.toUpperCase()}_H1_${side}.csv`);
  await fs.writeFile(file, csv(unique), "utf8");
  return {
    symbol: symbol.toUpperCase(),
    side,
    bars: unique.length,
    start: unique[0].time,
    end: unique.at(-1).time,
    file,
  };
}

await fs.mkdir(outputDir, { recursive: true });
const manifest = [];
try {
  for (const symbol of symbols) {
    for (const side of sides) {
      console.log(`Downloading ${symbol.toUpperCase()} ${side} H1 ${from.toISOString()} to ${to.toISOString()}`);
      manifest.push(await download(symbol, side));
    }
  }
  await fs.writeFile(
    path.join(outputDir, "manifest.json"),
    JSON.stringify({ provider: "Dukascopy", timeframe: "H1", from, to, downloads: manifest }, null, 2),
    "utf8",
  );
  console.log(JSON.stringify(manifest, null, 2));
} catch (error) {
  await fs.writeFile(
    path.join(outputDir, "download_error.json"),
    JSON.stringify({ message: String(error?.message ?? error), stack: String(error?.stack ?? ""), completed: manifest }, null, 2),
    "utf8",
  );
  throw error;
}
