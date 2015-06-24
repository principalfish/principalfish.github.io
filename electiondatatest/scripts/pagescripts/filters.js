// when user selects region this removes old vote list and replaces with selection
// problem with cache of tablesorter not being cleared result in multiple tables being displayed
// just rewriting the html as fix


function filterMap(){

	d3.selectAll(".map")
		.attr("opacity", 1)

	$("#keyonmap").html("");
	$('#seat-information').hide();

	$("#votesharebypartyselect option:eq(0)").prop("selected", true);
	$("#votesharechangebypartyselect option:eq(0)").prop("selected", true);
	$("#socialgradesselect option:eq(0)").prop("selected", true);

	var	party = filterStates[0].party;
	var gains = filterStates[1].gain;
	var region = filterStates[2].region;
	var majoritylow = filterStates[3].majoritylow
	var majorityhigh = filterStates[4].majorityhigh

	// change nonsense user inputs
	if (isNaN(majoritylow))
		majoritylow = 0;
	if (isNaN(majorityhigh))
		majorityhigh = 1000;

	// reset seatsAfterFilter from any previous filters.
	seatsAfterFilter = [];
	seatDataForChoropleth = {};

	g.selectAll(".map")
		.attr("id", "filtertime")

	if (party == "null")
		g.selectAll("#filtertime")
			.attr("id", "partyfiltered")
	else
		g.selectAll("#filtertime")
			.attr("style", function(d){
				if (party != seatData[d.properties.name]["seat_info"]["winning_party"])
					return "opacity: 0.02";
				})
			.attr("id", function(d){
				if (party == seatData[d.properties.name]["seat_info"]["winning_party"])
					return "partyfiltered"
				});

	if (gains == "null")
			g.selectAll("#partyfiltered")
				.attr("id", "gainfiltered")

	else
		if (gains == "gains")
			g.selectAll("#partyfiltered")
				.attr("style", function(d) {
					if (seatData[d.properties.name]["seat_info"]["change"] == "hold")
						return "opacity: 0.02";
					})
				.attr("id", function(d){
					if (seatData[d.properties.name]["seat_info"]["change"] == "gain")
						return "gainfiltered"
				});

		if (gains == "nochange")
			g.selectAll("#partyfiltered")
				.attr("style", function(d) {
					if (seatData[d.properties.name]["party"] != "gain")
						return "opacity: 0.02";
					})
				.attr("id", function(d){
					if (seatData[d.properties.name]["seat_info"]["change"] == "hold")
						return "gainfiltered"
				});

	if (region == "null")
		g.selectAll("#gainfiltered")
			.attr("id", "regionfiltered")

	else
		g.selectAll("#gainfiltered")
			.attr("style", function(d){
				if (region != seatData[d.properties.name]["seat_info"]["area"])
					return "opacity: 0.02";
			})
			.attr("id", function(d){
				if (region == seatData[d.properties.name]["seat_info"]["area"])
					return "regionfiltered"
			});

	g.selectAll("#regionfiltered")
		.attr("style", function(d) {
			if (parseFloat(seatData[d.properties.name]["seat_info"]["new_data"]["members"]) < majoritylow || parseFloat(seatData[d.properties.name]["seat_info"]["new_data"]["members"]) > majorityhigh )
				return "opacity: 0.02"

			})
		.each(function(d) {
			if (parseFloat(seatData[d.properties.name]["seat_info"]["new_data"]["members"]) >= majoritylow && parseFloat(seatData[d.properties.name]["seat_info"]["new_data"]["members"]) <= majorityhigh ){
				seatsAfterFilter.push(d);
				seatDataForChoropleth[d.properties.name] = seatData[d.properties.name];
				}
			});

	g.selectAll(".map")
		.attr("id", function(d) {
			return "i" + d.properties.info_id
		});


	generateSeatList();
	$(seatTotalContainer).html(" ");
	$(seatListContainer).html(" ");

	}

// resets all filters and map to default state
function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 1000;

	var previous_opacity = 1;
	var current_colour = 1;

	swingState = ["null", "null"];

	partyVoteShare = "null";
	partyVoteShareChange = "null";

	filterMap();

	$.each(seatData, function(seat){
		seatData[seat]["seat_info"]["current_colour"] = 1;
	})

	$(seatTotalContainer).html(" ");
	$(seatListContainer).html(" ");

	resetDropDowns();
}

function resetDropDowns(){
	$("#votesharebypartyselect option:eq(0)").prop("selected", true);
	$("#votesharechangebypartyselect option:eq(0)").prop("selected", true);
	$("#socialgradesselect option:eq(0)").prop("selected", true);

	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdowngains option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset();
}

// using seatsAfterFilter, generates list of filtered seats
function generateSeatList(){

	seatsAfterFilter.sort(function(a, b){
		var nameA = a.properties.name.toLowerCase(), nameB = b.properties.name.toLowerCase();
		if (nameA < nameB){
			return -1
		}
		if (nameA > nameB){
			return 1
		}
		return 0
	});

	$("#totalfilteredseats").html(" ");
	$("#filteredlisttable").html(" ");

	$(seatTotalContainer).html(" ");
	$(seatListContainer).html(" ");

	$(seatTotalContainer).append("<p>Total : " + seatsAfterFilter.length + "</p>");
	$.each(seatsAfterFilter, function(i){

		if (filterStates[1].gain == "gains" && filterStates[0].party != "null"){
						$(seatListContainer).append("<div onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">"
	          + "<div class=\"party-flair " + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["incumbent"] + "\"></div>"
	          + seatsAfterFilter[i].properties.name
	          + "</div>")
			}

		else{
	      $(seatListContainer).append("<div onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">"
	      + '<div class=\"party-flair ' + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["winning_party"] + '\"></div>'
	      + seatsAfterFilter[i].properties.name
	      + "</div>");
			}
	});
}
