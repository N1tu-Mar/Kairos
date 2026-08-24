import { Page } from "@/components/primitives";
import { LoadingBlock } from "@/components/states";

export default function Loading() {
  return (
    <Page>
      <LoadingBlock label="Loading inbox" />
    </Page>
  );
}
