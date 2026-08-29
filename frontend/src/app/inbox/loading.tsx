import { Page } from "@/components/primitives";
import { LoadingBlock } from "@/components/states";

/**
 * Streamed while this route’s server data is still being fetched.
 *
 * A skeleton rather than a blank region, so a slow load and a broken load
 * never look the same.
 */
export default function Loading() {
  return (
    <Page>
      <LoadingBlock label="Loading inbox" />
    </Page>
  );
}
