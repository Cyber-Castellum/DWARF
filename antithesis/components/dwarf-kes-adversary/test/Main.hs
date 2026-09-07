module Main (main) where

import Data.Bits (xor)
import Data.ByteString qualified as BS
import DwarfKesAdversary
    ( KesMutation (..)
    , describeKesMutation
    , gateKesMutation
    , mutateKesSignatureBytes
    , mutateKesSignatureSeeded
    )


main :: IO ()
main = do
    let original = BS.pack [0 .. 255] <> BS.replicate 448 0x5a
        mutated = mutateKesSignatureBytes original
    if BS.length mutated /= BS.length original
        then error "KES mutation changed encoded length"
        else pure ()
    if BS.init mutated /= BS.init original
        then error "KES mutation changed bytes outside signature tail"
        else pure ()
    if BS.last mutated /= (BS.last original `xor` 1)
        then error "KES mutation did not flip the final signature bit"
        else pure ()

    let seededA = mutateKesSignatureSeeded 0x20260906 1 original
        seededB = mutateKesSignatureSeeded 0x20260906 1 original
        description = describeKesMutation 0x20260906 1 original
    if seededA /= seededB
        then error "seeded KES mutation is not deterministic"
        else pure ()
    if BS.length seededA /= BS.length original
        then error "seeded KES mutation changed encoded length"
        else pure ()
    if BS.take (BS.length original - 448) seededA /= BS.take (BS.length original - 448) original
        then error "seeded KES mutation changed bytes outside the KES signature"
        else pure ()
    if seededA == original
        then error "rate=1 failed to mutate a sufficiently long header"
        else pure ()
    if mutateKesSignatureSeeded 0x20260906 0 original /= original
        then error "rate=0 mutated a header"
        else pure ()
    if not (kesMutationApplied description)
        then error "description disagrees with seeded mutation"
        else pure ()
    if kesSignatureOffset description < 0 || kesSignatureOffset description >= 448
        then error "description selected a byte outside the KES signature"
        else pure ()
    if kesMutationBit description < 0 || kesMutationBit description > 7
        then error "description selected an invalid bit"
        else pure ()

    let beforeBoundary = gateKesMutation 1800 1799 description
        atBoundary = gateKesMutation 1800 1800 description
    if kesMutationApplied beforeBoundary
        then error "minimum slot gate allowed an early mutation"
        else pure ()
    if atBoundary /= description
        then error "minimum slot gate suppressed a boundary mutation"
        else pure ()
