import Link from "next/link";
import { hasIngestedDocuments } from "@/lib/api";

export default async function Home() {
  const hasDocuments = await hasIngestedDocuments();

  return (
    <main className="min-h-screen flex flex-col items-center justify-center bg-transparent">
      <div className="max-w-6xl text-center flex flex-col items-center gap-8 sm:gap-14 px-6 sm:px-8">
        <div className="flex flex-col items-center gap-4">
          <h1 className="text-5xl sm:text-6xl md:text-8xl lg:text-[10rem] leading-none font-extrabold tracking-tighter">
            <span className="text-stark">Ops</span>
            <span className="text-crimson">Brain</span>
          </h1>
          <p className="text-ash-rose text-xs sm:text-sm tracking-[0.2em] sm:tracking-[0.3em]">
            INTELLIGENCE. AUTOMATED.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-4 sm:gap-8 w-full sm:w-auto">
          {hasDocuments ? (
            <>
              <Link
                href="/upload"
                className="bg-crimson text-stark px-12 py-5 rounded-lg font-bold text-lg text-center"
              >
                Upload more documents
              </Link>
              <Link
                href="/chat"
                className="bg-carbon text-crimson px-12 py-5 rounded-lg font-bold text-lg hover:bg-crimson/10 text-center"
              >
                Resume previous chat
              </Link>
            </>
          ) : (
            <Link
              href="/upload"
              className="bg-crimson text-stark px-12 py-5 rounded-lg font-bold text-lg text-center"
            >
              Upload
            </Link>
          )}
        </div>
      </div>
    </main>
  );
}
