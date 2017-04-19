var choro = {

  choroTypes : ["choro-voteshare", "choro-votesharechange", "swing"],

  handle: function(parameter, value){
    // reset choro selects not being used
    $.each(choro.choroTypes, function(i, choroType){
      if (choroType != parameter){
        $("#" + choroType + " option:eq(0)").prop("selected", true);
      }
    })


    parameter = parameter.slice(6);

    // for vote share change and swing attribute new class to each map obj
    if (value != "null"){
      var max = choro.minmax(parameter, value)
      choro.opacities(max, parameter, value);
      choro.applyClass(parameter, value);
      filters.display(); // reuse function to change opacities for map elements
      choro.keyOnMap(max, parameter, value);
    } else {
      // if null reset classses
      // sent code to filtres.filter so it triggers on filter change
      filters.filter();
    }
  },

  minmax : function(parameter, value){
    var max = 0;

    // for vote share parameter
    $.each(currentMap.seatData, function(seat, data){
      if (data.filtered == true){


        if (parameter == "voteshare"){
          // vote share choro
          if (value in data.partyInfo){
            if (data.partyInfo[value].total / data.seatInfo.turnout > max){
              max = data.partyInfo[value].total / data.seatInfo.turnout;
            }
          }
        } else if (parameter == "votesharechange"){
          // vote share change choro
          var current = 0;
          var previous = 0;

          if (value in data.partyInfo){
            current = data.partyInfo[value].total / data.seatInfo.turnout;
          }
          if (value in currentMap.previousSeatData[seat].partyInfo){
            previous = currentMap.previousSeatData[seat].partyInfo[value].total / currentMap.previousSeatData[seat].seatInfo.turnout;
          }
          var difference = current - previous;
          if (Math.abs(difference) > max){
            max = Math.abs(difference);
          }
        }

      }
    });

    if (max > 0.6){
      max = 0.6;
    }

    return max;
  },

  opacities: function(max, parameter, value){
    $.each(currentMap.seatData, function(seat, data){

      var opacity = 0; // default for filtered seats
      if (data.filtered == true && max != 0){
        if (parameter == "voteshare"){
          if (value in data.partyInfo){
            opacity = data.partyInfo[value].total / (data.seatInfo.turnout * max);
          }
        } else if (parameter == "votesharechange"){
          var current = 0;
          var previous = 0;
          if (value in data.partyInfo){
            current = data.partyInfo[value].total / data.seatInfo.turnout;
          }
          if (value in currentMap.previousSeatData[seat].partyInfo){
            previous = currentMap.previousSeatData[seat].partyInfo[value].total / currentMap.previousSeatData[seat].seatInfo.turnout;
          }
          var difference = current - previous;

          opacity = difference / max;
        }
      }

      if (opacity > 1){
        opacity = 1;
      }

      filters.opacities[seat] = opacity;
      data.mapSelect.opacity = opacity;
    });
  },

  applyClass : function(parameter, value){
    // voteshare
    if (parameter == "voteshare"){
      $.each(currentMap.seatData, function(seat, data){
        $("#i" + data.mapSelect.properties.info_id).removeClass()
        $("#i" + data.mapSelect.properties.info_id).addClass("map " + value)
      });
    }
    // vote share change
    else if (parameter == "votesharechange"){
      $.each(currentMap.seatData, function(seat, data){
        $("#i" + data.mapSelect.properties.info_id).removeClass()

        if (filters.opacities[seat] < 0){
          $("#i" + data.mapSelect.properties.info_id).addClass("map choro-minus");
          filters.opacities[seat] = Math.abs(filters.opacities[seat]);
          data.mapSelect.opacity = Math.abs(data.mapSelect.opacity);

        } else {
          $("#i" + data.mapSelect.properties.info_id).addClass("map choro-plus");
        }

      });
    }

  },

  keyOnMap: function(max, parameter, val){
    $("#keyonmap").empty();
    $("#keyonmap").show();

    var colour;
    if (parameter == "voteshare"){
      colour = $("." + val).css("background-color");

    } else if (parameter == "votesharechange"){
      // set to blue
      colour = "rgb(0, 0, 255)";
      // set val to "blue"
      val = "choro-plus"
    }

    var increment = 100 * (max) / 5;

    // add key twice including negative for vote share
    if (parameter == "votesharechange"){
      for (var i=5; i > 0; i--){
        var background = "rgba(255, 0, 0, " + i * 0.2 + ")";

        var toAdd = "<div class='choro-minus' style='background-color: " + background + "'>-"
                    + (i * increment).toFixed(1) + "%";

        if (max == 0.6 && i == 5){
          toAdd += "+";
        }

        toAdd += "</div>";

        $("#keyonmap").append(toAdd);
      }
    }

    for (var i=0; i < 6; i++){

      var background = colour.replace(")", "," + i * 0.2 +  ")").replace("rgb", "rgba");

      var toAdd = "<div class='" + val + "' style='background-color:" + background + "'>" +
      (i * increment).toFixed(1) + "%";

      if (max == 0.6 && i == 5){
        toAdd += "+"
      }
      toAdd += "</div>";

      $("#keyonmap").append(toAdd);
    }
  },

  reset: function(){
    $("#keyonmap").hide();
    $.each(currentMap.seatData, function(seat, data){

      $("#i" + data.mapSelect.properties.info_id).removeClass();
      $("#i" + data.mapSelect.properties.info_id).addClass("map " + data.seatInfo.current);
    });
    //reset choropleths selects
    $("#choro-voteshare option:eq(0)").prop("selected", true);
    $("#choro-votesharechange option:eq(0)").prop("selected", true);
  }

};
