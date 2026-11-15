# Spike S-06: captions side by side

50 COCO 2017 images (seed 6), captioned in half precision at batch size 8, three beams. CLIP is the cosine from `clip-vit-b-32-laion2b`; higher means the text matches the image better. Human captions are COCO's (CC BY 4.0); images are linked on Flickr under their own licences (`sources/coco10k-meta/images.jsonl`).

### 1. 000000000308.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | the people are sampling wine at a wine tasting. | 0.288 |
| Florence-2-base, <CAPTION> | A group of people standing around a table with wine glasses. | 0.231 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of people standing around a wooden table, each holding a glass of wine. On the right side of the image there is a wall, and in the background there are barrels, an umbrella, a board with text, plants, grass, hills and a clear blue sky. It appears to be a wine tasting event, with the people gathered around the table to enjoy the | 0.278 |
| BLIP-large | several people standing around a table with wine glasses on it | 0.240 |

### 2. 000000022360.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | very dark kitchen with siloette of a person standing near winddow | 0.336 |
| Florence-2-base, <CAPTION> | A woman standing in a kitchen next to a window. | 0.359 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows two people standing in a kitchen next to each other. On the left side of the image, there is a table with bottles, a laptop, and other objects placed on it. In the background, there are a few objects hanging on the wall and a window. | 0.289 |
| BLIP-large | there is a woman that is standing in the kitchen looking out the window | 0.328 |

### 3. 000000028131.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A boy on a skateboard doing a kick flip. | 0.332 |
| Florence-2-base, <CAPTION> | A young man doing a trick on a skateboard. | 0.327 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a young man wearing a green t-shirt, blue jeans, and black shoes, performing a trick on a skateboard in the air. In the background, there is a wooden fence, grass, trees, and a shed. | 0.355 |
| BLIP-large | there is a young man that is doing a trick on a skateboard | 0.319 |

### 4. 000000035985.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | Two people walking under an umbrella between parked cars. | 0.323 |
| Florence-2-base, <CAPTION> | A man walking in the rain with an umbrella. | 0.234 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man walking down a street in the rain, holding an umbrella in his hand. The street is lined with cars, trees, poles, and buildings, and the sky is visible in the background. The image is in black and white. | 0.308 |
| BLIP-large | there is a man that is standing next to a car in the snow | 0.277 |

### 5. 000000045175.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | a man riding a skateboard with his dog. | 0.283 |
| Florence-2-base, <CAPTION> | A man riding a skateboard with a dog on a leash. | 0.299 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man riding a skateboard down a road with a dog on a leash. He is wearing a green t-shirt, black shorts, and a black helmet. The background of the image is filled with trees, plants, and grass. | 0.320 |
| BLIP-large | there is a man riding a skateboard with a dog on a leash | 0.293 |

### 6. 000000077193.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | a close up of a dog near a frisbee | 0.317 |
| Florence-2-base, <CAPTION> | A brown and white dog standing next to a red bowl. | 0.327 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a brown and white dog standing on top of a grass covered field, with a red bowl in front of it. | 0.350 |
| BLIP-large | there is a white and brown dog standing next to a red frisbee | 0.338 |

### 7. 000000084162.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A white ferry boat transporting a lot of people. | 0.202 |
| Florence-2-base, <CAPTION> | A group of ships are docked in the water near a city. | 0.297 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of boats floating on top of a body of water, with people on board. In the background, there are buildings, trees, and a clear blue sky. | 0.254 |
| BLIP-large | there are many ships that are in the water near each other | 0.241 |

### 8. 000000089181.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | Guy playing in the park with his friends jumps to catch the frisbee | 0.238 |
| Florence-2-base, <CAPTION> | Two young men are playing frisbee in a park. | 0.290 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows two men playing frisbee on a grassy field, with one of them jumping in the air to catch the disc. In the background, there are vehicles on the road, trees, and a building. | 0.265 |
| BLIP-large | there are two men playing frisbee in a park with a building in the background | 0.310 |

### 9. 000000089835.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | Two chairs and a small birds below it | 0.318 |
| Florence-2-base, <CAPTION> | A bird sitting on top of a table next to a bench. | 0.297 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a bird perched atop a table with four chairs surrounding it, and a wall in the background. | 0.312 |
| BLIP-large | there is a bird that is sitting on a chair next to a table | 0.319 |

### 10. 000000090154.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A white bus turning on street with buildings in the background. | 0.275 |
| Florence-2-base, <CAPTION> | A white bus driving down a street next to tall buildings. | 0.243 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a white bus driving down a street lined with tall buildings. There are many people inside the bus, and traffic signals with poles on either side of the road. In the background, there are trees and a clear blue sky. | 0.242 |
| BLIP-large | there is a bus that is driving down the street in the city | 0.252 |

### 11. 000000093183.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | This sewing room space is small but well stocked. | 0.348 |
| Florence-2-base, <CAPTION> | A sewing machine sitting on top of a table next to a chair. | 0.333 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a sewing room with a sewing machine sitting on top of a table next to a chair. On the table there are books, papers, and other objects, and on the right side of the image there is a table with a cloth draped over it. In the background there are frames attached to the wall, a clock, and a cupboard with various objects on it. There | 0.340 |
| BLIP-large | there is a sewing machine sitting on top of a table next to a chair | 0.335 |

### 12. 000000137884.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A female tennis player swinging her tennis racket. | 0.289 |
| Florence-2-base, <CAPTION> | A woman in a black dress playing tennis on a court. | 0.319 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows Serena Williams in action at the Australian Open, wearing a black dress and holding a tennis racket in her hand. She is standing on the court, ready to hit the ball. | 0.263 |
| BLIP-large | woman playing tennis on a tennis court with a racket in her hand | 0.265 |

### 13. 000000184396.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A kitchen with a sliding glass door and a table next to it. | 0.312 |
| Florence-2-base, <CAPTION> | A kitchen with a table and chairs in it. | 0.272 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a kitchen with a table and chairs in it. On the left side of the image there is a microwave oven, cupboards, and other objects on the countertop. In the background there are frames attached to the wall and a glass door leading to a balcony with a railing and trees visible outside. | 0.342 |
| BLIP-large | there is a small kitchen with a table and chairs in it | 0.290 |

### 14. 000000184745.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A public bus is stopped at a bust stop. | 0.284 |
| Florence-2-base, <CAPTION> | A green and white bus parked next to a body of water. | 0.343 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a green and white bus parked on the side of a road next to a body of water. On the right side of the image there is a railing, and in the background there are houses, trees, hills and a clear blue sky. | 0.354 |
| BLIP-large | there is a green bus that is parked on the side of the road | 0.314 |

### 15. 000000188163.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A group of men riding on a raft in the ocean. | 0.289 |
| Florence-2-base, <CAPTION> | A group of people in a small boat in the ocean. | 0.286 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of people in a small boat on the water, wearing life jackets and helmets. On the right side of the image, there is a crane with a hook attached to it, and a flag is visible in the background. | 0.287 |
| BLIP-large | there is a boat full of people riding in the water | 0.283 |

### 16. 000000189101.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A large long train on a steel track. | 0.271 |
| Florence-2-base, <CAPTION> | A train pulling into a train station next to a platform. | 0.311 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a diesel-electric locomotive pulling into a train station next to a platform. On the left side of the image, there is a platform with a few people standing on it, and on the right side there are railings. In the background, there are sheds and a clear blue sky. | 0.324 |
| BLIP-large | there is a train that is pulling into the train station | 0.285 |

### 17. 000000191770.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A woman standing on the top of a hill filled with dry grass. | 0.328 |
| Florence-2-base, <CAPTION> | A woman flying a kite on top of a hill. | 0.312 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a woman standing on top of a hill, flying a kite in the sky. She is surrounded by lush green grass and plants, and there is another person standing nearby. The sky is visible in the background, and the image has been edited with borders. | 0.315 |
| BLIP-large | there is a man that is flying a kite in the sky | 0.288 |

### 18. 000000231617.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A red and yellow bus sits on the back of a flatbed truck, driving down the highway. | 0.301 |
| Florence-2-base, <CAPTION> | A red and white bus on a flatbed trailer on a highway. | 0.343 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a red double decker bus being towed by a flatbed trailer on a road surrounded by a railing, trees, and a sky with clouds in the background. | 0.329 |
| BLIP-large | there is a red bus on a flatbed on a highway | 0.319 |

### 19. 000000236897.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | The cooks prepared the meal int he large kitchen. | 0.324 |
| Florence-2-base, <CAPTION> | Two men in a kitchen preparing food on a counter. | 0.325 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows two men in a kitchen preparing food. They are wearing black aprons and caps, and are standing in front of a table with various objects on it. To the right of the table is a wooden cupboard with various items on top. In the background, there are utensils hanging on the wall, a stove, a microwave oven, a sink, a tap, | 0.354 |
| BLIP-large | there are two men in the kitchen preparing food in the kitchen | 0.314 |

### 20. 000000248194.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | There is a man reading a paper and two people talking to a officer | 0.296 |
| Florence-2-base, <CAPTION> | A man standing on a sidewalk looking at his cell phone. | 0.228 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man standing on the right side of the frame, holding a mobile phone in his hand and looking at it intently. In the middle of the picture, a police officer wearing a helmet is standing and holding a bag, while a woman wearing a black t-shirt and carrying a red bag is walking on the road. On the left side, two men and a woman | 0.322 |
| BLIP-large | there is a man looking at his cell phone on the street | 0.219 |

### 21. 000000252198.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | Hot dogs and drinks sitting on a wooden table. | 0.229 |
| Florence-2-base, <CAPTION> | A wooden table topped with plates of food and drinks. | 0.285 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a wooden table with plates of food and drinks on it, along with forks, knives, spoons, glasses with drinks, flower vases, and other objects. In the background, there is a wall with a frame attached to it. | 0.280 |
| BLIP-large | there are two plates of food on a table with a vase of flowers in the background | 0.323 |

### 22. 000000259044.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A crosswalk with people crossing during a red light. | 0.274 |
| Florence-2-base, <CAPTION> | A city street with traffic lights and people crossing the street. | 0.311 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a city street with cars and people crossing the street. There are light poles, traffic signals, sign boards, and buildings with windows lining the street, and trees in the background. The sky is visible in the distance. | 0.323 |
| BLIP-large | there is a traffic light hanging over a street in a small town | 0.312 |

### 23. 000000276945.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A kitchen with a stove an dove and wooden counter. | 0.258 |
| Florence-2-base, <CAPTION> | A stove top oven sitting on top of a wooden counter. | 0.324 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a kitchen with a stove top oven sitting on top of a wooden counter, surrounded by cupboards and a mat on the floor. On the countertop, there is a cover and a few other objects. In the background, there are a few objects visible. | 0.294 |
| BLIP-large | there is a stove top oven sitting on top of a wooden counter | 0.344 |

### 24. 000000302498.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A man is diving towards the ground to catch a Frisbee. | 0.297 |
| Florence-2-base, <CAPTION> | A group of men playing a game of frisbee on a field. | 0.293 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of men playing a game of frisbee on a grassy field. Some of the players are wearing caps, while others are sitting on chairs. In the background, there are tents, vehicles, trees, and a clear blue sky. | 0.267 |
| BLIP-large | they are playing frisbee in a park with many people watching | 0.223 |

### 25. 000000317851.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A motorcycle parked in front of a store selling clothing. | 0.313 |
| Florence-2-base, <CAPTION> | A motorcycle parked on the side of a street next to a car. | 0.256 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a car parked next to a motorcycle on the side of a street, with clothes hanging out to dry. In the background, there is a person sitting on a bench with clothes draped over it, and another person standing nearby. There is also a watermark on the image. | 0.366 |
| BLIP-large | there is a motorcycle that is parked in front of a car | 0.265 |

### 26. 000000340706.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A cut in half sandwich sitting on top of a piece of tin foil. | 0.333 |
| Florence-2-base, <CAPTION> | A sandwich with meat and onions wrapped in foil. | 0.324 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a hot dog sitting on top of a silver foil-covered table. The hot dog is a combination of brown and white colors, and the foil is a silver color. | 0.341 |
| BLIP-large | there is a hot dog on a bun with onions on it | 0.215 |

### 27. 000000341718.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | You can barely see the people on the shore who are flying kites. | 0.312 |
| Florence-2-base, <CAPTION> | A group of people flying kites over a beach. | 0.341 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of people kitesurfing on the beach, with the water lapping against the shore and the sand beneath them. In the background, there are trees and buildings on a hill, and the sky is visible at the top of the image. | 0.336 |
| BLIP-large | there are many kites flying in the sky over a beach | 0.338 |

### 28. 000000342432.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A man is surfing through the wave near other swimmers. | 0.272 |
| Florence-2-base, <CAPTION> | A man riding a snowboard down the side of a snow covered slope. | 0.240 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man riding a snowboard down the side of a snow covered slope. He is wearing a black outfit and blue shoes, and the snow is glistening in the light. | 0.273 |
| BLIP-large | there is a man riding a wave on a surfboard in the ocean | 0.270 |

### 29. 000000354976.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | Laptop lifted up on a desk next to another monitor. | 0.298 |
| Florence-2-base, <CAPTION> | A woman sitting at a desk in front of a computer. | 0.232 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a woman sitting at a desk in front of a computer, surrounded by a variety of items on the table including a laptop, mobile phone, mouse, speakers, wires, and other objects. On the right side of the image, there is a window, and on the left side, there are frames attached to the wall. | 0.257 |
| BLIP-large | there is a woman sitting at a desk with a laptop and a monitor | 0.222 |

### 30. 000000392928.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A bathroom door is opened by a toilet. | 0.326 |
| Florence-2-base, <CAPTION> | A bathroom with a toilet and a shower stall. | 0.307 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a white toilet sitting in a bathroom next to a wooden door. The toilet seat is flush and there is a switchboard attached to the wall in the background. | 0.346 |
| BLIP-large | there is a bathroom with a toilet and a bathtub in it | 0.309 |

### 31. 000000396548.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A Christmas tree sitting in a living room. | 0.341 |
| Florence-2-base, <CAPTION> | A living room with a christmas tree in the corner. | 0.347 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a living room filled with furniture, including a sofa with cushions, stools, and a table with various objects on it. There is a Christmas tree decorated with lights and ornaments, a chair, a lamp, a ladder, photo frames on the wall, and decorative items on the ceiling. In the background, there are windows with window blinds. | 0.313 |
| BLIP-large | there is a living room with a christmas tree in the corner of the room | 0.347 |

### 32. 000000406047.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A skier pushing off to head down the course. | 0.307 |
| Florence-2-base, <CAPTION> | A man in a red suit skiing down a snow covered slope. | 0.276 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man in a red suit skiing down a snowy hill, holding ski poles in his hands. In the background, there are a number of people, flags, and boards with words written on them. On the right side of the image, there is a person holding a camera. | 0.300 |
| BLIP-large | there is a man that is skiing down a hill in the snow | 0.244 |

### 33. 000000411362.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A group of three people standing next to each other. | 0.243 |
| Florence-2-base, <CAPTION> | Three people standing next to each other in a room. | 0.252 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows three people standing next to each other in a room. The man in the middle is wearing a black blazer, white shirt, and black tie, and is holding an object in his hand. The two women on either side of him are wearing black dresses. In the background, there are several photo frames attached to the wooden wall, a person sitting in a chair, a | 0.265 |
| BLIP-large | there are three people standing next to each other in a room | 0.240 |

### 34. 000000426857.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A large white bus drives on the street out side during the day. | 0.304 |
| Florence-2-base, <CAPTION> | A white bus parked in a parking lot. | 0.300 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a white bus parked in a parking lot next to a building, surrounded by other vehicles on the road. In the background, there are trees and a clear blue sky. | 0.288 |
| BLIP-large | there is a white bus that is parked in a parking lot | 0.297 |

### 35. 000000451038.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A person cutting up a piece of fruit with a knife. | 0.322 |
| Florence-2-base, <CAPTION> | A person cutting an apple with a knife. | 0.355 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a person cutting an apple with a pair of scissors. The person's hand is visible, gripping the apple firmly as they carefully cut it. The apple is a bright red color, and the scissors are silver. | 0.336 |
| BLIP-large | there is a person holding a knife over an apple with a bite taken out of it | 0.343 |

### 36. 000000465424.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A group of bikers sitting out in front of a store near a street. | 0.338 |
| Florence-2-base, <CAPTION> | A group of people standing around parked motorcycles on the side of the road. | 0.330 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of people standing on the side of a street, some of them holding bicycles. There are also vehicles on the road, a tree, buildings with windows, name boards, lights, and other objects in the background. | 0.280 |
| BLIP-large | there are a lot of bikers that are lined up on the street | 0.356 |

### 37. 000000467109.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | Person pointing to the tray of donuts behind the display window | 0.321 |
| Florence-2-base, <CAPTION> | A display case filled with lots of different types of doughnuts. | 0.343 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a person holding a tray of doughnuts in front of a display case filled with different types of donuts. The person's hand is visible on the left side of the image, and there are trays of food items on the table. The background is slightly blurred, giving the image a soft focus. | 0.342 |
| BLIP-large | several trays of doughnuts are on display in a bakery | 0.353 |

### 38. 000000469152.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A teddy bear sticking out of a postal shipping box. | 0.431 |
| Florence-2-base, <CAPTION> | A teddy bear sitting on top of a mail box. | 0.364 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a brown teddy bear sitting atop a white box, with a white t-shirt underneath. The background of the image is a wall. | 0.323 |
| BLIP-large | there is a brown teddy bear sitting on top of a mail box | 0.361 |

### 39. 000000483639.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A man in shorts hits a tennis ball with a racket. | 0.319 |
| Florence-2-base, <CAPTION> | A man swinging a tennis racquet at a tennis ball. | 0.337 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man swinging a tennis racquet at a ball on a clay court surrounded by a mesh fencing with trees and plants in the background. | 0.329 |
| BLIP-large | there is a man that is playing tennis on the court | 0.279 |

### 40. 000000494904.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A group of men standing on a baseball field. | 0.222 |
| Florence-2-base, <CAPTION> | A group of baseball players standing next to each other. | 0.232 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of baseball players standing next to each other on a field, with one of them holding a bat. There is a mesh fencing in the foreground, and in the background there are many people sitting on chairs. At the bottom of the image, there is text. | 0.275 |
| BLIP-large | there are many people standing in the stands at a baseball game | 0.307 |

### 41. 000000517296.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | People standing outside of a blue and white bus. | 0.298 |
| Florence-2-base, <CAPTION> | A blue bus parked on the side of a street. | 0.249 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a blue bus parked on the side of a street next to a group of people wearing orange jackets. There is a dustbin in front of the bus and a board with something written on it. In the background, there are trees, buildings with windows, and a clear blue sky. | 0.317 |
| BLIP-large | there is a bus that is parked on the side of the street | 0.271 |

### 42. 000000517987.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | a man that is sitting on a skateboard and drinking something | 0.329 |
| Florence-2-base, <CAPTION> | A man sitting on a skateboard holding a red cup. | 0.324 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man with a long beard sitting on a skateboard, holding a red cup in his hand. He is wearing a blue t-shirt, jeans, and shoes, and in the background there is a building. | 0.409 |
| BLIP-large | there is a man with a beard sitting on a skateboard with a cup of coffee | 0.390 |

### 43. 000000530690.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A woman is riding a three wheeled motorcycle. | 0.331 |
| Florence-2-base, <CAPTION> | A woman riding a motorcycle with a sidecar on the back. | 0.322 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a woman riding on the back of a motorcycle with a sidecar. She is wearing a white dress and the motorcycle is black and white. In the background, there is a railing, a pole, and a few buildings. The sky is visible at the top of the image. | 0.350 |
| BLIP-large | there is a woman that is riding a motorcycle on the street | 0.271 |

### 44. 000000539335.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A group of business people stand and listen to a man. | 0.268 |
| Florence-2-base, <CAPTION> | A group of people standing in an office talking. | 0.301 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a group of people standing around a table in an office. The man in the center is holding a book and a pen, while the others are looking at him attentively. On the table in front of them are various objects, and on the wall behind them are boards with text. The room is illuminated by lights on the ceiling. | 0.284 |
| BLIP-large | there are many people standing in a room talking to each other | 0.275 |

### 45. 000000549711.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A computer monitor and keyboard set up on top of the treadmill. | 0.336 |
| Florence-2-base, <CAPTION> | A computer desk with a keyboard and mouse on it. | 0.343 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a computer desk with a keyboard and mouse sitting on top of it. The desk is situated in front of a wall with a glass window, through which we can see trees, grass, and a road. | 0.306 |
| BLIP-large | there is a computer desk with a keyboard and mouse on it | 0.344 |

### 46. 000000551518.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A man standing in a  lush green field flying a kite. | 0.329 |
| Florence-2-base, <CAPTION> | A man flying a kite on top of a lush green field. | 0.331 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a man wearing a blue t-shirt and brown pants standing on a grassy field, holding a kite in his hand. In the background, there are light poles, trees, buildings, and a clear blue sky. | 0.319 |
| BLIP-large | there is a man that is flying a kite in a field | 0.301 |

### 47. 000000559950.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | An adorable dog looking out the side of a car door window. | 0.273 |
| Florence-2-base, <CAPTION> | A dog is looking out the window of a car. | 0.270 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a small pekingese dog sitting in the passenger seat of a car, looking out the window. In the background, there are other vehicles on the road, a person walking on the footpath, light poles, sign boards, trees, buildings, and a clear blue sky. | 0.350 |
| BLIP-large | there is a dog that is looking out the window of a car | 0.287 |

### 48. 000000562029.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A grand round mirror hangs over a vanity in a motel bathroom. | 0.338 |
| Florence-2-base, <CAPTION> | A bathroom with a sink, toilet and a mirror. | 0.246 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a bathroom with a wash basin with taps, a platform with bottles and other items, a mirror on the wall with lights and a photo frame, towels on a hanger, a toilet with a tissue roll, and a door. | 0.249 |
| BLIP-large | there is a bathroom with a sink, mirror, and toilet in it | 0.276 |

### 49. 000000574776.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A close-up view of a hand holding a smartphone. | 0.299 |
| Florence-2-base, <CAPTION> | A person holding a cell phone in their hand. | 0.278 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a person holding a cell phone in their hand, with a cable connected to it. The phone appears to be a cellular phone, with its sleek design and glossy black finish. The cable is likely used to connect the phone to a computer or other device. | 0.310 |
| BLIP-large | there is a person holding a cell phone in their hand | 0.273 |

### 50. 000000581715.jpg

| Source | Caption | CLIP |
|---|---|---|
| Human | A pizza sitting on top of a metal pan. | 0.292 |
| Florence-2-base, <CAPTION> | A pizza sitting on top of a metal pan on a table. | 0.286 |
| Florence-2-base, <DETAILED_CAPTION> | The image shows a pizza sitting on top of a metal plate on a table, surrounded by bowls, plates, spoons, tissue papers, and other objects. | 0.238 |
| BLIP-large | there is a pizza that is sitting on a pan on a table | 0.279 |
