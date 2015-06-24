// functions to generate choropleths + keys

function choroplethInitiator(value, type){


  if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		$("#keyonmap").html("")
		loadmap();
	}

  else if (filteredSeatsTotal > 0){

		if (type == "swing" && (swingState[0] == "null" || swingState[1] == "null")){
			null
		}

		else {

	    maxMin = getChoroplethMaxMin(value, type);

	    var max = maxMin[0];
	    var min = maxMin[1];

	    generateChoropleth(value, type, max, min);
		}
  }
}

function getChoroplethMaxMin(value, type){
  var values = [];

  $.each(seatDataForChoropleth, function(seat){

    var data;
    if (type == "percent" || type == "change"){

      if (value in seatDataForChoropleth[seat]["party_info"]){
        data = seatDataForChoropleth[seat]["party_info"][value][type];
        }
      else {
        data == undefined;
      }
    }

  	else if (type == "swing"){
			if (value[0] in seatDataForChoropleth[seat]["party_info"] && value[1] in seatDataForChoropleth[seat]["party_info"]){
				data = (seatDataForChoropleth[seat]["party_info"][value[1]]["change"] - seatDataForChoropleth[seat]["party_info"][value[0]]["change"]) / 2;
			}
		}

    if (data != undefined){
      values.push(data);
      }

  });

  var max = Math.max.apply(Math, values);
  var min = Math.min.apply(Math, values);

  if (type == "change" || type == "swing"){
    var max = Math.max(Math.abs(max), Math.abs(min));
    var min = -max;
  }

  if (type == "percent"){

		if (max > 60){
			max = 60;
		}
  }

  return [max, min];

}

var choroplethColour;

function generateChoropleth(value, type, max, min){

  var range = max - min;

  var textColour;

  if (type == "percent"){
    var relevant_class = "." + value;
    choroplethColour = $(relevant_class).css("background-color");
    textColour = $(relevant_class).css("color");
  }

  else if (type == "change" || type =="swing"){
    choroplethColour = "rgb(0, 0, 255)";
    textColour = "white";
  }

  else {
    choroplethColour = "rgb(255, 0, 0)";
    textColour = "white";
  }

  $.each(seatDataForChoropleth, function(seat){

    var map_id = seatDataForChoropleth[seat]["seat_info"]["info_id"];
    var seat = seatsFromIDs[map_id];

    var styleInfo = getChoroplethOpacity(value, type, max, min, range, seatsFromIDs[map_id], choroplethColour);
    var colour = styleInfo[0];
    var opacity = styleInfo[1];

    d3.select(map_id)
      .style("fill", colour)
      .attr("opacity", opacity);
  });

  keyOnMap(value, type, max, min, range, choroplethColour, textColour);
}

function getChoroplethOpacity(value, type, max, min, range, seat, choroplethColour){

  var opacity;
  var dataPoint;

  if (type == "percent" || type == "change"){
    if (value in seatDataForChoropleth[seat]["party_info"]){
      dataPoint = seatDataForChoropleth[seat]["party_info"][value][type];
    }

    else {
      return [null, 0]
    }

    // for vote change negative changes
    if (type == "change" && dataPoint < 0){
      choroplethColour = "red";
      }

    // for percent vote
    if (type == "percent"){
      opacity = (dataPoint - min) / range;
      seatData[seat]["seat_info"]["current_colour"] = opacity;
    }
    // for change in vote percent
    else if(type == "change"){
      opacity = Math.abs(dataPoint / max);
    }
  }

  else if (type == "swing"){

		if (value[0] in seatDataForChoropleth[seat]["party_info"] && value[1] in seatDataForChoropleth[seat]["party_info"]){

	    dataPoint = (seatDataForChoropleth[seat]["party_info"][value[1]]["change"] - seatDataForChoropleth[seat]["party_info"][value[0]]["change"]) / 2;

			if (type == "swing" && dataPoint < 0){
	      choroplethColour = "red";
	      }

	  	opacity = Math.abs(dataPoint / max);
		}

		else {
			return [null, 0]
		}

  }

  seatData[seat]["seat_info"]["current_colour"] = opacity;

  return [choroplethColour, opacity];

}


function keyOnMap(value, type, max, min, range, choroplethColour, textColour){

  $("#keyonmap").html("");

  var title;

  if (type == "percent"){
    title = partylist[value] + " Vote Share";
  }

  if (type == "change"){
    title = partylist[value] + " Vote Share Change";
  }

  if (type == "swing"){
    title =  "Swing between " + partylist[value[0]] + " and " + partylist[value[1]];
  }

  $("#maptitletext").text(title);
  $("#maptitle").show();


  if (value == "null"){
		null
	}

  else {
    var gap = range / 5;
    var opacities = {};

    for (var i = 0; i < 6; i++){
      var num = (min + gap * i);
      var opacity = (num - min) / range;

      if (type == "change" || type == "swing"){
        num = (max - (max / 5) * i);
        opacity = num / max

      }

      if (["percent", "change", "swing"].indexOf(type) > -1){
        num = num.toFixed(1);
      }
      else {
        num = num.toFixed(0);
      }

      if (type == "percent" && num == 60.0){
        num = num + "+";
      }

      opacities[num] = opacity;

    }

    // % sign for some
    var percentageSign;
    if (["percent", "change", "swing"].indexOf(type) > -1){
      percentageSign = "%"
    }

    else {
      percentageSign = ""
    }


    $.each(opacities, function(num){
      var colour = choroplethColour.replace(")", "," + opacities[num] +  ")").replace("rgb", "rgba")

      $("#keyonmap").append("<div style=\" color:"
      + textColour + "; text-align: center; background-color: "
      + colour + "\">"
      + num + percentageSign + "</div>");

    });

    if (type == "change" || type == "swing"){
      var opacities = {};


    	for (var i = 1; i < 6; i++){
    		var num = (max / 5 * i);

    		var opacity = (num) / max;
    		num = num.toFixed(1);

    		opacities[num] = opacity;
    	}

    	$.each(opacities, function(num){
    		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(255, 0, 0, " + opacities[num] + "); color: white;\">-"
    		+ num + "%</div>");
    	});
    }
  }
}
