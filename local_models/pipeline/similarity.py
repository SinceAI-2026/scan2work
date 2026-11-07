"""Similarity: whether two touching instances are one object, by DINOv2 image embeddings.

Pieces of one thing look alike, touch in 3D and are seen together. Twins (two identical pumps side
by side) do too, so the merge itself needs 3D evidence that they are one: views where one SAM mask
holds both (cluster.py's view consensus) in at least MERGE_CONSENSUS of the frames that see both.
Look-alikes the views keep apart are reported as twins.

An earlier version also carried names from confident instances to uncertain look-alikes over a DINOv2
kNN graph (label propagation, Zhou et al. 2003, as in ZLaP, Stojnic et al. CVPR 2024). It named no
object on this room in two runs, so it was removed.
"""
LOOK_ALIKE = 0.8  # DINOv2 cosine above which two instances look alike
MIN_TOUCH = 0.1  # share of the smaller instance's voxels next to the other one
MIN_CO_OBSERVERS = 3  # frames that see both, for any 3D verdict on the pair
MERGE_CONSENSUS = 0.5  # frames with one mask holding both / frames seeing both, to merge


def merge_verdict(cosine, touch, observers, supporters):
    """For a touching pair: 'merge', 'twin' (look-alike, but the views keep them apart) or None."""
    if cosine < LOOK_ALIKE or touch < MIN_TOUCH or observers < MIN_CO_OBSERVERS:
        return None
    return "merge" if supporters >= MERGE_CONSENSUS * observers else "twin"
