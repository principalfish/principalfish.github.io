// for voteshare

function voteShare(){
	var value = partyVoteShare;

	var relevant_class = "." + value;
	var colour = $(relevant_class).css("background-color");
	var text_colour = $(relevant_class).css("color");

	if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		$("#keyonmap").html("")
		loadmap();
	}

	else {


		var	max = 0;
		var min = 100;
		$.each(seatData, function(seat){
			if (value in seatData[seat]["party_info"]){
				var percentage = seatData[seat]["party_info"][value]["percent"];
				if (percentage > 0){
					if (percentage > max){
						max = percentage;
					}
					if (percentage < min){
						min = percentage;
					}
				}
			}
		})

		if (min > 20){
			min = 20;
		}

		if (max > 60){
			max = 60;
		}


		for (var i = 0; i < 651; i++){
			var id_vote_share = "#i" + i;

			d3.select(id_vote_share)
				.style("fill", colour)
				.attr("opacity", function(d) {
					var seat_name = seatsFromIDs[id_vote_share]
					if (value in seatData[seat_name]["party_info"]){
						var vote_share = seatData[seat_name]["party_info"][value]["percent"]
					}
					else {
						var vote_share = 0
					}

					var vote_range = max - min;
					vote_share = vote_share - min;
					if (vote_share < 0) {
						vote_share = 0
					}

					seatData[seat_name]["seat_info"]["current_colour"] = vote_share / vote_range

					return vote_share / vote_range;

				})
		}

		if (previousnode != undefined){
			var last_seat = seatData[seatsFromIDs[previousnode]];
			if (value in last_seat["party_info"] ){
				previous_opacity = (last_seat["party_info"][value]["percent"] - min) / (max - min);
				}

			else {
				previous_opacity = 0;
				}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

	}
	keyOnMap(value, max, min, colour, text_colour);
	colour = null;

}


function keyOnMap(value, max, min, colour, text_colour){

	var orig_color = colour;

	$("#keyonmap").html("");

	if (value == "null"){
		null
	}

	else {

		var vote_range = max - min;
		var gap = vote_range / 5;
		var opacities = {};

		for (var i = 0; i < 6; i++){
			var num = (min + gap * i);
			var opacity = (num - min) / vote_range;
			num = num.toFixed(1);

			if (num == 60.0){
				num = num + "+";
			}
			opacities[num] = opacity;
		}

		$.each(opacities, function(num){
			colour = colour.replace(")", "," + opacities[num] +  ")").replace("rgb", "rgba")

			$("#keyonmap").append("<div style=\" color:"
			+ text_colour + "; text-align: center; background-color: "
			+ colour + "\">"
			+ num + "%</div>");

			colour = orig_color;

		});
	}
}

function swingKeyOnMap(max){
	$("#keyonmap").html("");

	var gap = max / 5;
	var opacities = {};

	for (var i = 0; i < 6; i++){
		var num = (max - gap * i);

		var opacity = (num) / max;
		num = num.toFixed(1);

		opacities[num] = opacity;
	}


	$.each(opacities, function(num){
		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(0, 0, 255, " + opacities[num] + "); color: white;\">+"
		+ num + "%</div>");
	})

	var opacities = {};

	for (var i = 1; i < 6; i++){
		var num = (gap * i);

		var opacity = (num) / max;
		num = num.toFixed(1);

		opacities[num] = opacity;
	}

	$.each(opacities, function(num){
		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(255, 0, 0, " + opacities[num] + "); color: white;\">-"
		+ num + "%</div>");
	})
}

function voteShareChange(){
	var value = partyVoteShareChange;

	var relevant_class = "." + value;

	if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove();
		$("#keyonmap").html("");
		loadmap();
	}

	else {

		var	max = 0;
		var min = 0;
		$.each(seatData, function(seat){
			if (value in seatData[seat]["party_info"]){
				var percentage = seatData[seat]["party_info"][value]["change"];
				if (percentage > max){
					max = percentage;
					}
				if (percentage < min){
					min = percentage;
					}
				}
			})

		if (Math.abs(min) > max){
			max = Math.abs(min);
		}

		for (var i = 1; i < 651; i++){
			var id_vote_share = "#i" + i;

			var seat_name = seatsFromIDs[id_vote_share];

			if (seat_name != undefined){

				var change;

				if (value in seatData[seat_name]["party_info"]){
					change = seatData[seat_name]["party_info"][value]["change"]
					}
				else  {
					change = 0;
				}

				if (change >= 0){
					d3.select(id_vote_share)
						.style("fill", "blue")
						.attr("opacity",function(d){
							seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(change) / max;
							return Math.abs(change) / max;
						});
				}

				else {
					d3.select(id_vote_share)
						.style("fill", "red")
						.attr("opacity",function(d){
							seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(change) / max;
							return Math.abs(change) / max;
						});
				}

			}
		}
		if (previousnode != undefined){

			var last_seat = seatData[seatsFromIDs[previousnode]];
			if (value in last_seat["party_info"]){
				previous_opacity = (Math.abs(last_seat["party_info"][value]["change"]) / max);
			}
			else {
				previous_opacity = 0;
			}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");


		colour = null;

		swingKeyOnMap(max);
	}
}

function partyMembers(){
	var	max = 0;
	var min = 1000;
	$.each(seatData, function(seat){

			var members = parseFloat(seatData[seat]["seat_info"]["new_data"]["members"]);
			if (members > max){
				max = members;
				}
			if (members < min){
				min = members;
				}

		});

	if (Math.abs(min) > max){
		max = Math.abs(min);
	}

	var range = max - min

	for (var i = 1; i < 651; i++){
		var id_vote_share = "#i" + i;

		var seat_name = seatsFromIDs[id_vote_share];

		if (seat_name != undefined){

			var members = seatData[seat_name]["seat_info"]["new_data"]["members"];
			d3.select(id_vote_share)
				.style("fill", "red")
				.attr("opacity",function(d){
					seatData[seat_name]["seat_info"]["current_colour"] = (Math.abs(members) - min) / range;
					return (Math.abs(members) - min) / range;
				});

			}
		}

	if (previousnode != undefined){
		var last_seat = seatData[seatsFromIDs[previousnode]];

		previous_opacity = (parseFloat(last_seat["seat_info"]["new_data"]["members"]) / max );


		}



	flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

	var colour = $(".labour").css("background-color")
	partyMembersKey(max, min, colour);
	colour = null;

}

function partyMembersKey(max, min, colour){

	var orig_color = colour;

	$("#keyonmap").html("");

	var vote_range = max - min;
	var gap = vote_range / 5;
	var opacities = {};

	for (var i = 0; i < 6; i++){
		var num = (min + gap * i);
		var opacity = (num - min) / vote_range;
		num = num.toFixed(0);

		opacities[num] = opacity;
	}

	$.each(opacities, function(num){
		colour = colour.replace(")", "," + opacities[num] +  ")").replace("rgb", "rgba")

		$("#keyonmap").append("<div style=\" color:"
		+ "white" + "; text-align: center; background-color: "
		+ colour + "\">"
		+ num + "</div>");

		colour = orig_color;

	});

}


function socialGrades(){
	var value = socialGrade;
	var	max = 0;
	var min = 100;

	$.each(seatData, function(seat){

			var percentage = parseFloat(seatData[seat]["seat_info"]["new_data"][value]);
			if (percentage > max){
				max = percentage;
				}
			if (percentage < min){
				min = percentage;
				}

		});

	if (Math.abs(min) > max){
		max = Math.abs(min);
	}
	var range = max - min

	for (var i = 1; i < 651; i++){
		var id_vote_share = "#i" + i;

		var seat_name = seatsFromIDs[id_vote_share];

		if (seat_name != undefined){

			var percentage = seatData[seat_name]["seat_info"]["new_data"][value];
			d3.select(id_vote_share)
				.style("fill", "red")
				.attr("opacity",function(d){
					seatData[seat_name]["seat_info"]["current_colour"] = (Math.abs(percentage) - min) / range;
					return (Math.abs(percentage) - min) / range;
				});

			}
		}

	if (previousnode != undefined){
		var last_seat = seatData[seatsFromIDs[previousnode]];

		previous_opacity = (parseFloat(last_seat["seat_info"]["new_data"]["members"]) / max );


		}



	flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

	var colour = $(".labour").css("background-color")
	partyMembersKey(max, min, colour);
	colour = null;
}
