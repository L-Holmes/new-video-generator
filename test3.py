
from sentence_splitter import split_text_into_sections

script = """One of the best places on Earth to find whale fossils… is a desert.

Not near a desert.

In one.

In Egypt, there’s a valley filled with ancient whale skeletons sitting directly in the sand — ribs, vertebrae, entire fossilized bodies baking under one of the driest climates on Earth.

Which feels impossible until you realize the desert used to be an ocean.

And once that clicks, deserts start getting weird very fast.

Because some of the driest places humans can stand today were underwater for millions of years. Entire seas vanished. Ecosystems disappeared. Then time buried everything under dust and rock until the planet basically erased the evidence.

Except occasionally… bones stick out.

Wadi Al-Hitan in Egypt — literally “Valley of the Whales” — looks almost cinematic from above. Endless pale desert, rolling sand, rocky outcrops.

Then the camera drops lower and suddenly there’s a whale spine in the middle of nowhere.

Not a small fossil either.

Full skeletons over 15 meters long scattered across the desert floor.

And these whales are incredibly important because they capture evolution mid-transition. Some species still had tiny back legs — remnants from when whale ancestors walked on land millions of years earlier.

That’s the surreal part.

You can stand in one of the driest landscapes on Earth looking at the skeleton of a creature that evolved for the ocean… while surrounded by terrain that hasn’t seen meaningful rain in ages.

Your brain struggles to connect the two realities.

Desert.

Whale.

They shouldn’t overlap.

But around 40 million years ago, this entire region was covered by the Tethys Sea. Warm shallow water filled with marine life. Then geology shifted, oceans retreated, land rose upward, climates changed.

And eventually the sea disappeared completely.

Leaving whales stranded not physically…

…but historically.

That’s what fossils really are: moments trapped after the environment around them moved on.

And nowhere makes that clearer than the Atacama Desert in Chile.

Because the Atacama is so dry it almost preserves things too well.

Some weather stations there have gone years without recording rainfall. Parts of the landscape look genuinely Martian — cracked earth, red rock, salt flats, almost no vegetation.

Yet buried within this hyper-arid desert are enormous marine fossil deposits.

Whales.

Fish.

Ancient sea creatures.

Entire graveyards from ecosystems that vanished millions of years ago.

And scientists still debate exactly how some of these whale concentrations formed because certain sites contain huge numbers of skeletons packed together unusually densely.

One theory suggests toxic algal blooms poisoned large groups of marine animals repeatedly over time. Their bodies drifted into coastal areas, became buried in sediment, then eventually fossilized as the landscape transformed completely around them.

Which creates this eerie effect where the desert feels like it’s remembering being underwater.

That’s the strange emotional power of marine fossils in deserts. They expose deep time in a very physical way.

Normally humans experience landscapes as permanent.

Deserts feel ancient.

Oceans feel ancient.

But whale bones in the sand reveal both are temporary.

The Sahara went through this too.

Right now people imagine the Sahara as endless dunes and unbearable heat. But parts of it were once green — rivers, lakes, vegetation, entire ecosystems supporting massive wildlife populations.

There were crocodiles there.

Hippos.

Fish.

And marine fossils scattered through regions that are now brutally dry.

The Earth changes climates so aggressively over geological timescales that places can completely switch identities.

Ocean becomes desert.

Forest becomes wasteland.

Seabed becomes mountain range.

And humans are very bad at emotionally understanding timescales large enough for that transformation to happen.

That’s why these fossils feel uncanny.

Because they compress millions of years into one image.

A whale skeleton in sand instantly forces your brain to imagine impossible environmental change.

And honestly, deserts preserve that evidence beautifully.

No dense forests hiding fossils.

Very little rainfall eroding them away quickly.

Just exposed landscapes where ancient bones slowly emerge from the ground like the Earth is uncovering its own memory.

Some skeletons at Wadi Al-Hitan are so well preserved that you can clearly trace the shape of the spine through the desert surface. Ribs curve outward into the sand almost like the animal died recently.

Except it died before humans existed.

Before ice ages.

Before modern continents looked the way they do now.

That scale is hard to process.

And what’s fascinating is how often humans accidentally discover these sites.

Construction crews.

Miners.

Travelers.

People crossing empty landscapes suddenly noticing shapes in rock that turn out to be ancient vertebrae or skull fragments.

Because once you know what to look for, deserts start revealing evidence of entirely different worlds hidden beneath them.

That’s also why aerial footage works so well for this topic.

From ground level, fossils can look fragmented and abstract.

From above, suddenly you see the outline of giant marine creatures embedded into endless dry terrain. Skeletons stretched across landscapes where oceans no longer exist.

It almost looks like the planet glitched.

And in a way, it did.

Because Earth’s surface is far less stable than humans instinctively assume. Coastlines move. Seas disappear. Climates flip dramatically. Entire ecosystems get erased and replaced repeatedly over deep time.

We just happen to live during one temporary version of the map.

Which means someday, millions of years from now, some future species might stand in another desert looking at fossils from our oceans wondering how water ever existed there at all.

And honestly, that might be the creepiest part.

Not that there are whale bones in deserts.

That deserts themselves are only temporary too."""

out = split_text_into_sections(script)
print(out)
